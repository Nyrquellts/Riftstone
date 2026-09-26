# Enemy skins: one chimera white, the rest vanilla

Some placements of an enemy wear a different texture set while every other one stays vanilla. The
first family is the **chimera** (`em5200`), and the first skins are Dragon's Dogma Online's
**White**, **Shadow** and **Blaze** chimeras. Measured on DDDA.exe build 2364871 (2026-09-24). The
files and the hooks are proven outside the game (below). **What it looks like in game is UNKNOWN until
someone plays it.**

## Why a plugin is needed

The chimera's resources are not chosen by data. `uEm5200::setup` registers a fixed 85-entry resource
list (`0x0184E290`: model, motions, collision, params, and the full-detail material `e5200_a` with its
textures). `uEm5200::setupSecond` (vtable slot 33, called from `uObjModel::sync` once the unit's
resources have loaded) then fetches `model\em\e52\e5200\e5200_a` by that literal path (`0x009A1428`)
and calls `setMaterial` on that one unit. It binds its damage textures from a fixed table
(`0x016FF548`, 11 entries of {material index, 4 texture paths}) at `0x009A1553`. The goat head
(`uEm5200_00`) and snake tail (`uEm5200_01`) are separate units created in the parent's `setupSecond`.
They do the same with `e5200_00_a` / `e5200_01_a` and their own tables.

A resource is identified by its path, so two different files at the same path cannot both be loaded.
A chimera can only look different if it *asks for different paths*. The six places it names a
skinnable path are what the plugin changes.

## How a placement asks for a skin

`cSetInfoPawn::applyInfo` (`0x007A4B80`) copies each stat multiplier's flag and value into the unit
whatever the flag says. For magick defence that is flag `+0x20BC`, value `+0x20C0`. Every reader of
the value (`uEnemy::callbackDamagePreCalc` `0x00AAA6B7`, and the player, human-enemy and NPC
versions) tests the flag first. So a placement whose magick-defence multiplier is **off** can carry
any bits in the value without changing a single stat. Skin *NN* is the bits `0x534B00NN` ("SK" + the
number; 1..99). `riftstone world`/`lot` YAML labels such a value:

```yaml
    魔法防御力倍率設定の有無: 0  # use the magick defence multiplier
    魔法防御力の変化倍率: 871878400000.0  # enemy skin 1: an enemy_skins marker; the multiplier is off, so no stat changes
```

Other markers were ruled out:
- `mName` is not copied to the unit.
- The chimera's own flag is normalised to 0/1 on load (`0x008B2922`).
- A scale marker could be overwritten by the enemy's params.

## The skin folder

Skin *NN* lives in the chimera's own archive, `rom/enemy/em5200.arc`, so it loads with every chimera:

| Resource | What |
|---|---|
| `model\em\e52\e5200\sNN\e5200_a` (`.mrl`) | the body's full-detail material: vanilla, with the four maps below pointed into `sNN` |
| `model\em\e52\e5200\sNN\e5200_00_a`, `e5200_01_a` (`.mrl`) | the goat head's and snake tail's, likewise |
| `model\em\e52\e5200\sNN\e5200_skin_BM`, `_face_BM`, `_hebi_BM`, `_eye_BM` (`.tex`) | the albedo maps (`hebi` holds the snake and the goat head) |

Normal maps, masks, the damage alphas and the burnt-fur map stay vanilla. Each skin adds about 4 MB
at DDO's 1024 px resolution.

## The plugin (`native/plugins/enemy_skins`)

It installs six `jmp` hooks, and every one resumes the game at the next original instruction:

| Site | Original | For a marked unit |
|---|---|---|
| `0x009A1428` body material | `push "…\e5200_a"; push rMaterial` | the path `…\sNN\e5200_a` |
| `0x009A1553` body damage table | `add eax, 0x016FF548` | the skin's copy of the table |
| `0x009B17DD` goat material | `push "…\e5200_00_a"; push rMaterial` | `…\sNN\e5200_00_a` |
| `0x009B19D0` goat damage table | `add eax, 0x016F9C08` | the skin's copy |
| `0x009B96CD` snake material (unit in `ebp`) | `push "…\e5200_01_a"; push rMaterial` | `…\sNN\e5200_01_a` |
| `0x009B99C4` snake damage table | `add ebx, 0x016F3D08` | the skin's copy |

The damage tables are copied at start with the four albedo paths pointed into `sNN`; the CMM, AM
and burnt-fur entries are kept. The parts take their parent's skin (goat `+0x7278`, snake `+0x72C0`).
A unit is a chimera only if its vtable is `uEm5200`'s (`0x015C4E28`; the parts `0x015C5FD0`,
`0x015C6C68`), so the Gorechimera, which is table variant 1, and every other class are never
touched.

Before patching, the plugin checks all six sites byte for byte, the three vtables' `setupSecond` slots,
the material strings and every texture pointer in the tables. On any difference it patches nothing
and says why in `riftstone\logs\enemy_skins.log`. Original code, no third-party source.

### What happens when something is missing

| State | Result |
|---|---|
| The plugin without marked placements | changes nothing |
| A marked placement without the plugin | a plain chimera |
| A marked placement without the skin's files | that chimera keeps its low-detail material, because `rResourceManager::get` returns null and the game skips `setMaterial` |

## Proof (static, plus the real code in a harness)

- **Harness, the real code on fake chimeras** (`native/plugins/enemy_skins/test`, part of
  `tools\test_all.cmd`). `skins_stub.exe` takes DDDA.exe's fixed range as its own image, and
  `skins_harness_core.dll` maps a read-only copy of DDDA.exe over it and loads the plugin, which
  verifies and patches that copy. The harness then enters each material site with a fake chimera, goat
  or snake and a fake resource manager, and calls each real damage helper. 26 checks pass:
  - the paths requested for skins 1, 7 and 42;
  - vanilla for an unmarked unit, the flag on, skin 0 or 100, another class, a part with no parent,
    and the Gorechimera block;
  - every register the game relies on intact at the call.
- **`tests/test_skins.py`** covers:
  - marker round trip and refusals;
  - DDO→DDDA texture conversion, including a truncated texture refused;
  - the skin's materials pointing exactly the four maps into the folder;
  - an encounter that wears a skin and copies another group's conditions;
  - on this machine, the plugin's sites read from its source against the owner's DDDA.exe.
- **`tests/test_studiofiles.py`** and **`tests/test_texcodec.py`** cover Studio's side: a skin into a
  new mod, a number another mod has refused (and no mod left behind), previews, downloads, an edited
  PNG back in as the same texture kind with the old file kept, set aside and back (with a swap), the
  zips; the PNG reader on every colour type, depth and row filter; the BC1/BC3 encoder.
- **Fuzzing:**
  - `skintex` (new): refuses cleanly, or returns a DDDA texture that parses, holds exactly its mips,
    converts to `.dds` and is idempotent.
  - `encounter`: gains `like`/`skin` and the rule that a new group's number and layout name are free
    in every enemy group list of the stage.
  - `png` and `studio_files` (new): a picture is refused cleanly or read as exactly its pixels, which
    write back the same; Studio's file routes never write outside the workspace, never lose a file's
    content, and leave every mod file loadable as its kind.
  - 120 s on the touched targets: 0 findings.

## Dragon's Dogma Online's chimeras

DDO's chimeras (`EM015200`–`EM015204` in its `rom\EM`) are DDDA's chimera model with other maps.
The UV layouts are identical, checked side by side on the skin, snake/goat and face sheets.
`tools/ddo_skins.py` reads DDO's client through the `ddon` toolkit (`<path>`) and writes a folder
of textures for `riftstone skin make`.

| Variant | DDO archive | How it is made | Faithful? |
|---|---|---|---|
| White Chimera | `EM015202` | DDO's own four albedo maps; `skin make` rewrites the revision (0x9D → 0x99). DDO and DDDA share the header and mip layout: every word but the first is equal. | **exact** textures |
| Shadow Chimera | `EM015203` | DDO's curse shader blends a dark texture over the normal colours through a mottled mask (`em015203_*_d_MM`). DDDA has no such shader, so the blend is baked in: DDO's own masks and snake/goat and eye maps, with a dark violet chosen here (DDO's material constants are unlabeled). | approximation, not animated |
| Blaze Chimera | `EM015204` | the same masks; scorched fur with ember light added where the mask is brightest | approximation, not animated |

The textures are game data from the owner's clients. They stay in the mod folder (`mods/` is
git-ignored) and in scratch, never in git.

## Using it

```bat
python tools\ddo_skins.py white C:\temp\white
riftstone new "mods\DDO Chimeras"
riftstone skin make chimera 1 --textures C:\temp\white --title "White Chimera" --source "DDO em015202" --mod "mods\DDO Chimeras"
riftstone encounter 443 chimera --count 1 --at 1329,-1345,-1271 --like 5 --skin 1 --mod "mods\DDO Chimeras"
riftstone skin list --mod "mods\DDO Chimeras"
```

`--like 5` copies group 5's conditions. In stage 443 (a post-game Everfall level: a round room with a
curved stair), groups 0–19 are alternative monster sets on one spot, and each loads only on its own lot
flag (60–79). Group 5 is the Drake's set (flag 65), so the chimera appears exactly when the Drake does.
Your own paint works too: put `e5200_skin_BM.png` (or `.dds`, and the others) in a folder and point
`--textures` at it. `riftstone skin export chimera 1 --mod "mods\DDO Chimeras" --out C:\temp\edit`
writes a skin's four maps as pictures named so they go straight back in.

### In Studio

- **Skins tab.** Every skin in your mods, with its four maps as thumbnails. *New skin* takes your own
  pictures (any of the four maps; the rest stay the game's) or one of DDO's variants, and puts it in
  one of your mods or a new, separate one (created only when the skin is written). *Pictures (.png)*
  downloads a skin's maps as a `.zip` to paint; *Replace pictures…* makes the same skin again from the
  edited ones, and the files it replaces are kept in the mod's `aside/_history/`.
- **Skin numbers are shared by every mod.** The game sees one `sNN` folder per family, whichever mod
  added it, so Studio gives a new skin the first number no mod uses and refuses one another mod has.
- **A mod's Files panel** (Mods tab, *Files*) shows each skin file as *adds* (a skin never clashes),
  previews any texture, downloads it as `.png`, `.dds` or the raw `.tex`, and takes an edited picture
  back as the same kind of texture (the old one kept). A BC1 map the game draws fully opaque stays
  opaque whatever alpha the picture has; a BC3 map (the chimera's albedo maps) keeps the picture's
  alpha.
- **Put aside** moves a file into the mod's `aside/` folder: kept, not built. Setting aside a skin's file
  while placements still wear that skin is allowed, and Studio warns: what the game draws then is
  UNKNOWN.
- The World tab's encounter form has a **Skin** field (and **Copy group** for `--like`); the plan warns
  when the skin is in no mod yet, or comes from another mod that must be installed too.

To play it, which needs the owner's yes because it writes to the game:
1. `riftstone loader install`
2. `riftstone loader plugin add native\plugins\enemy_skins\out\enemy_skins.asi`
3. `riftstone install "mods\DDO Chimeras"`

## Found on the way

- **First run in the game (2026-09-24): a crash in the archive, not in the plugin.** Both plugins
  loaded and patched (their logs say so). Then, in the open world, a vanilla chimera spawned and the
  game stopped with "Fatal error: Failed open file ...\nativePC\model\em\e52\e5200\s01\e5200_hebi_BM.tex".
  The game loads `em5200.arc` in order whenever any chimera appears, and a material looks up its
  textures as it loads. The rebuilt archive had the skins' materials ahead of their textures
  (`s01\e5200_00_a` before `s01\e5200_hebi_BM`), so the lookup missed and the game went for a loose
  file. The game's own archives list textures first. `mod.build_archive` now orders added resources
  the same way (regression test in `tests/test_skins.py`). Reinstalled with the fix, the owner
  reported that it works (2026-09-25); how each skin looks in game is still UNKNOWN.

- **A stage can have more than one enemy group list, sharing one number space.** Stage 443 has
  `st443_e` and the DLC's `st443_e_dlc01` (groups 20–22, in `rom/dl1/stage/stage443/stage443_set`). A
  layout's name carries only the group number, so `encounter` must pick a number free in every list.
  It used to check only the base list, and would have written a new group 20 over the DLC's
  `st443_00m00n_e20`. Fixed and tested; `encounter` also refuses any layout name that already exists.
- The post-game Everfall's random boss is a group load condition: `mLoadCondition.mLotFlag = 1` with a
  distinct `mDataLotFlag.mFlagNo` per set.

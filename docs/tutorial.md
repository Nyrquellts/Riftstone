# Riftstone — the full tutorial

A modding suite for **Dragon's Dogma: Dark Arisen** (Steam PC). This walks the
whole thing end to end, from zero to a working mod, then through every tool.
No programming needed for the common paths.

> Everything here has been verified against the real game: archives and the
> data formats round-trip byte-for-byte, and every entry point is fuzz-tested.
> Where something is **read-only** or **partial**, it says so plainly.

---

## Contents

1. [Install](#1-install)
2. [The 60-second tour](#2-the-60-second-tour)
3. [Concepts: archives, resources, YAML](#3-concepts)
4. [Studio — the browser app](#4-studio)
5. [Editing parameters (stats, AI/FSM, effects)](#5-editing-parameters)
6. [Reading and editing AI state machines (.fsm)](#6-ai-state-machines)
7. [Dialogue and text (.gmd)](#7-dialogue-and-text)
8. [Custom items](#8-custom-items), then [moving enemies and objects](#8b-moving-enemies-and-objects-lot)
9. [Editing collision (.ocl)](#9-editing-collision-ocl), then [textures and reskins](#9b-textures-and-reskins-tex)
10. [Building and installing mods](#10-mods)
11. [The loader: overlay, crash logs, plugins](#11-the-loader)
12. [Raising the enemy/unit limit](#12-raising-the-enemyunit-limit)
13. [Command reference](#13-command-reference)
14. [What is proven vs. in progress](#14-status)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Install

1. Install [Python 3.11+](https://www.python.org/downloads/) — tick **“Add
   python.exe to PATH.”**
2. Put the `riftstone` folder anywhere. `Riftstone.cmd` is the launcher.
3. Check everything:

   ```bat
   Riftstone.cmd doctor
   ```

   It finds the game through Steam. If it can’t, add `--game "D:\path\to\DDDA"`
   to any command, or set the `RIFTSTONE_GAME` environment variable.

First run builds a resource index (~30 s, once). After that, search is instant.

Your mods live in one mods folder: the folder your installed mods are in, else the `mods` folder beside
Riftstone when it has mods, else `Documents\Riftstone\mods` (set `RIFTSTONE_MODS` to choose another).
Studio shows that folder, `Riftstone.cmd new "<name>"` makes a mod there, and every command takes a mod
there by its name: `--mod "Harder Goblins"`. A folder path still works everywhere. `Riftstone.cmd mods`
lists your mods and says which are installed.

---

## 2. The 60-second tour

Make goblins hit harder, in four commands:

```bat
Riftstone.cmd find goblin
Riftstone.cmd new "Harder Goblins"
Riftstone.cmd extract charparam/em/em0100_cmn.prp --mod "Harder Goblins"
Riftstone.cmd install "Harder Goblins"
```

`find goblin` shows the Goblins (`em0100`), Hobgoblins, Grimgoblins, Greater Goblins and Goblin Shamans,
each with the files that shape it: stats first (`charparam/em/em0100_cmn.prp`), then attacks and AI.
Before `install`, open `files\charparam\em\em0100_cmn.prp.yaml` in the mod's folder (`Riftstone.cmd mods`
shows where) in any text editor. The game names the fields in Japanese, and the YAML writes the English
name beside each one Riftstone knows: change `攻撃力: 250.0  # Attack` to `400.0`. To undo everything:

```bat
Riftstone.cmd restore
```

Prefer buttons? `Riftstone.cmd studio` opens the whole thing in your browser.

---

## 3. Concepts

- **Archive (`.arc`)** — a zip-like container. The game has 8,536 of them under
  `nativePC`. Riftstone unpacks and repacks them losslessly.
- **Resource** — one file inside an archive (a model, texture, parameter table,
  AI script…). Identified by its engine name + a type. There are 144 types.
- **YAML** — Riftstone turns the editable resource types into readable text you
  change and save. Converting back is **byte-exact**: an untouched file rebuilds
  to the original bytes, and an edit changes only what you edited.

You rarely touch archives directly. You put loose files in a **mod**, and
Riftstone patches them into the right archives on install, keeping verified
backups.

---

## 4. Studio

```bat
Riftstone.cmd studio
```

Opens `http://127.0.0.1:<port>` in your browser. It runs on your PC only;
nothing is uploaded. Three tabs:

- **Explore** — search all 358,431 resources by name or extension
  (`em0100`, `shl`, `em0100.shl`). Green = editable. Click one to preview it and
  **Add to mod**. State machines preview as readable pseudo-code (Readable /
  YAML); layouts show a top-down map of their placements.
- **Mods** — toggle mods on/off, open an editable file in the built-in editor
  (live error-checking with line numbers), **Save & install**. A layout file
  opens with its map: drag a placement to move it.
- **Game** — install/remove the loader, read crash reports, and **Restore
  original files** in one click.

The editor validates as you type: e.g. `mByte must be between 0 and 255`, with
the exact line highlighted. Nothing installs until the file is valid.

---

## 5. Editing parameters

Most tunable data is XFS, which becomes YAML. This covers **enemy/player stats,
AI state machines (FSM), projectiles/effects (SHL), camera, shops, quests** and
more — 44 resource types, all byte-exact.

Get a file as YAML:

```bat
Riftstone.cmd param enemy.statusparam        # a file on disk -> enemy.statusparam.yaml
Riftstone.cmd extract param/status/enemy.statusparam --mod "My Mod"   # from the game, into a mod
```

Example — a status table:

```yaml
root:
  _class: rStatusParam
  mArray:
    - _class: cStatusParam
      mCategory: 1
      mTimer: 240.0        # was 180.0 — longer effect
      mParam0: 5.0
```

Rules: whole numbers in integer fields, decimals or whole numbers in float
fields; lists are `- item` lines or `[a, b]`; keep the `_class:` lines and the
structure. Save, then `install` (or use Studio’s Save & install).

---

## 6. AI state machines

Quests, scripted events, NPC schedules and a lot of monster behaviour are
state machines (`.fsm`, 3,073 of them). Riftstone reads them two ways:

```bat
Riftstone.cmd fsm quest/q0005_b00.fsm          # readable: states, actions, conditions
Riftstone.cmd extract quest/q0005_b00.fsm --mod "My Mod"   # editable YAML, byte-exact
```

The readable view (also in Studio: open any `.fsm`, then **Readable / YAML**):

```
state int  [id 0, start]
  ->   st210 (id 1)                 when c5: StageNo == 210
state st210  [id 1]
  runs a sub-machine:
  |  state q05_095ck  [id 0, start]
  |    do   QuestFlag      cFSMOrderParamQuestFlag  mQuestNo=5 mBitNo=95 mFlagBool=1
  |    ->   s0000 (id 1)                 when c0: FreeFlag[0] is set
...
conditions:
  c2: FreeReal[0] > 180
  c4: GSF[374] is set
```

- A **state** does its `do` actions, then leaves along the first `->` whose
  condition holds.
- **Conditions** are expressions: `==, !=, <, <=, >, >=, and, or, not`,
  `is set` (a flag) and `has bits` (a flag word). NPC schedules read like
  `GameHour >= 7 and GameHour < 19`.
- Every number you need for an edit is printed: state ids (`id 1`), condition
  ids (`c5`). Find the same ids in the YAML (`mId: 1`, `mConditionId: 5`),
  change them, save, install.

Typical edits: a boss's immortal/phase flag, a camera lock, waiting on a
timer (`FreeReal[0] > 180`), "spawn the next wave when the last is dead"
(Undead Gauntlet style), or pointing a message order at a line you added
(next section).

---

## 7. Dialogue and text

Every line of text in the game (dialogue, item names and descriptions, menus,
quest logs, pawn chatter) is a `.gmd` file: 7,954 of them, all 7 languages,
all byte-exact.

Find where a line lives:

```bat
Riftstone.cmd text find "west gate"
  id/npc_wind/stage/st100_eng.gmd  id 173  You'll not get through here. Best use the west gate.
```

Add a new line, in **every language at once** (so ids stay the same across
languages):

```bat
Riftstone.cmd text add id/npc_wind/stage/st100_eng.gmd "The gate is sealed by royal decree." --mod "My Mod"
  english   id 266   files/id/npc_wind/stage/st100_eng.gmd.yaml
  french    id 266   files/id/npc_wind/stage/st100_fre.gmd.yaml
  ...
✔ Added line 266 to 7 language versions.
```

Then translate it in each language file if you like. Edit existing lines
directly in the YAML:

```yaml
messages:
  - id: 173
    text: "You'll not get through here. Best use the west gate."
```

Rules Riftstone enforces for you:

- **Add lines at the end.** Other data finds a line by its id; inserting or
  deleting in the middle would renumber everything after it, so Riftstone
  refuses a file whose ids moved. To retire a line, set its text to `""`.
- Line breaks are `\r\n` (the game's usual). Tags like `<ICON …>`,
  `<ITNO …>` and `{Herr}{Herrin}` (gendered forms) belong to the game; keep
  them.

**Using a new line in game.** Something must point at its id:

- Stage messages: `cScenarioArg_Message.mMesId` in
  `scr/st###/etc/st###_mes.sce` indexes `id/npc_wind/stage/st###` (measured
  on every reference in the game). Point one at your new id.
- Quest/event message orders in FSMs (`cFSMOrderParamMessage`: type, quest,
  id): which text file a type/quest pair selects is **not measured yet**, so
  test in game.

Item names and descriptions are `id/message/item/itemName_*.gmd` and
`itemInfo_*.gmd`; the next section fills them for you.

---

## 8. Custom items

A new item in one command: named in all 7 languages, priced, weighed, and on
sale in a shop.

```bat
Riftstone.cmd items new "Rift Tonic" --like Greenwarish --description "Brewed from rift-touched herbs." --buy 500 --weight 0.2 --shop n007ShopList --mod "My Mod"
✔ Added item 71 "Rift Tonic", a copy of Greenwarish (43) with its own name, description, price and weight
✔ On sale in files/etc/shop/n007ShopList.shp.yaml, 3 in stock
```

What happens:

- The game's item list has **175 unused "Unknown Item" slots**. The new item
  takes the first one (or `--id N`), so the list never grows past the 1,901
  entries the game's icons are known to handle.
- `--like` copies an existing item's whole record, so the new item **behaves
  like that item** (a curative like Greenwarish, a material, …); you give it
  its own name, description, price (`--buy`; selling defaults to 40%) and
  weight.
- The name and description go into all 7 languages at that item's id.
- `--shop` (or later, `items shop <item> --shop <shop>`) adds it to a shop's
  stock with no conditions. `riftstone find ShopList --type shp` lists the
  shops.

Browse and check:

```bat
Riftstone.cmd items list greenwarish              # id, name, weight, prices
Riftstone.cmd items list --free                   # the unused slots
Riftstone.cmd items list --mod "My Mod"           # the list as your mod has it
```

Everything lands in the mod as editable files (`etc/item/itemList.itl.yaml`,
the text files, the shop), so you can keep tuning by hand:

```yaml
  - id: 71
    name: "Rift Tonic"      # for orientation; names live in the text files
    weight: 0.2
    buy: 500
    sell: 200
    raw: 00000000...        # the rest of the record, byte for byte
```

**Craft it.** Add a recipe (either order of ingredients counts as the same
pair, so an existing pair is refused):

```bat
Riftstone.cmd items recipe Greenwarish "Wolf Pelt" --makes "Rift Tonic" --count 2 --mod "My Mod"
```

**Make enemies drop it.** Find a drop set by something it already drops,
then put your item in it. The chance comes out of the set's chance of
nothing, so the set still adds up to the same total:

```bat
Riftstone.cmd items sets "Wolf Pelt"
  set     3  Small Fang 16%, Wolf Pelt 30%, Sour Scrag of Beast 12%, nothing 40%, Rift Fragment 2%
Riftstone.cmd items drop "Rift Tonic" --set 3 --percent 10 --mod "My Mod"
  ✔ Rift Tonic now drops from enemy set 3 (10%); the set adds up to 100%
```

`--table reward` works on the reward and gathering table instead. The whole
tables are editable YAML too (`etc/item/ItemEmListSetTbl.ist`,
`etc/item/itemSetTbl.ist`, `etc/item/itemMix.imx`), each slot written as
`[item, weight, "name"]`. Which enemies use which set is **not known yet**:
finding a set by its drops is the way in.

**Not done yet:** new *equipment*. Weapon and armour stats per upgrade level
live in `etc/item/LvParam*.itemlv` (binary, not decoded), so a sword copied
with `--like` may lack its stats in game. Consumables and materials don't
use those tables. Whether a new item needs anything beyond the item list,
its text and a shop is **untested in game**: try one and tell us.

---

## 8b. Moving enemies and objects (.lot)

Every stage places its enemies, NPCs and objects from layout files (`.lot`,
6,209 of them). Riftstone decodes **every record in every one of them**, with
the grammar read from `DDDA.exe`'s own loaders: position, angle and scale, and
every setting the game stores per placement, by the engine's own names.

**Find your way first.** The world map says which file is which:

```bat
Riftstone.cmd world stages                   :: every stage, its rooms, how much it places
Riftstone.cmd world stage 100                :: its enemy groups: who, how many, where they stand
Riftstone.cmd world enemy "direwolf"         :: every layout that places direwolves
Riftstone.cmd world group 100 e 143          :: one group: its settings, files and placements
```

A layout's name says whose placements it holds: `st100_45m55n_e143` is **group
143** of stage 100's enemy list (`st100_e.gpl`) in map cell 45m55n
(`docs/world-map.md`).

```bat
Riftstone.cmd open scr/st100/etc/st100_45m55n_e143.lot     # what it places, and where
Riftstone.cmd extract scr/st100/etc/st100_45m55n_e143.lot --mod "My Mod"
```

```yaml
records:
  - id: 2
    class: cSetInfoEnemy0201  # kind 8, enemy
    mWolfType: 0
    mIsRandamScale: 1
    mWolfScale: 1.0
    ...
    mEmItemTable: -1  # drop set (row of etc/item/ItemEmListSetTbl)
    mExperienceOW: 0  # EXP override (0: the enemy's own)
    ...
    HP倍率設定の有無: 0  # use the HP multiplier
    HPの倍率: 0.0  # HP multiplier
    ...
    mName: "em0201"
    mOrder: 4
    mPosition: [57013.645, 42716.332, -44312.414]
    mAngle: [-0.0, 1.741311, 0.0]  # radians
    mScale: [1.0, 1.0, 1.0]
    mDrawDistance: -1.0  # -1: default
    mIsOnSplitAreaIgnore: 0
```

Each record is one thing the game places: its `id`, its `class` (what kind of
placement: `cSetInfoEnemy0201` is a direwolf's, `cSetInfoNpc` an NPC or a
bandit, `cSetInfoOmModel` an object), then every field the game's loader reads,
in its order. The comments translate the developers' Japanese field names.
Change what you like and install: move an enemy (`mPosition`), turn it
(`mAngle`, radians), make an **elite** (`HP倍率設定の有無: 1` and `HPの倍率: 2.5`
give that one placement 2.5x HP; attack, defence and magick work the same
way), give it its own drop set (`mEmItemTable`) or EXP (`mExperienceOW`), or
change a chest's loot (`mSetTableID` on an object names an `itemSetTbl` set).

**Or drag it on a map.** In Studio, a layout file shows a top-down map above
its YAML: a dot per placement (red enemies, green NPCs and hostile humans, blue
objects), a tick for the way it faces, and a grid in game units. In the editor
(Mods tab, open the `.lot.yaml`), **drag a dot** to move that placement:
Riftstone rewrites its `mPosition` (x and z; the height stays), checks the
file, and you save. Everything else in the file stays byte for byte.

**Add and remove enemies.** Copy an existing record (it keeps everything about
the original: what it spawns and how) and put it somewhere new, or remove one:

```bat
Riftstone.cmd spawns list scr/st100/etc/st100_45m55n_e143.lot
Riftstone.cmd spawns copy scr/st100/etc/st100_45m55n_e143.lot 0 --at 58600,42716,-45360 --mod "My Mod"
Riftstone.cmd spawns remove scr/st100/etc/st100_45m55n_e143.lot 1 --mod "My Mod"
```

In Studio: click a dot on the map, then **Duplicate** (the copy appears
nearby, selected, ready to drag) or **Remove**. A copy gets a new id (the
largest in the file + 1, kept inside the 0..1023 the game's loader can index);
removing one leaves every other id as it was, in case other data refers to
them. This works in **every** layout; checked on all 6,209: copy then remove
gives back the original file byte for byte. **In game: untested.**

**A whole encounter: 100 goblins.** A stage's groups live in its group lists,
`scr/st<stage>/etc/st<stage>_e.gpl` for enemies (`_n` NPCs and hostile humans,
`_p` objects, `_t` AI sensor targets). The game builds its own hordes as a
group with a **spawn cap** and **respawn type 5**: the cap is how many come in
total, the placements are the spawn points it refills (stage 330's group 35
sends 100 goblins from 7 points). `encounter` builds exactly that for you:

```bat
Riftstone.cmd new "Goblin Horde"
Riftstone.cmd encounter 706 goblin --count 100 --at group:2 --mod "Goblin Horde"
Riftstone.cmd install "Goblin Horde"
```

It copies the group standing nearest the spot (`--at x,y,z`, or `group:N` for
the middle of group N), so the new group acts where that one acts, gives the
copy your enemy and the horde settings, writes 10 spawn points (`--at-once`;
about 10 enemies are the most the game keeps active at once) each copied from a
real placement of that enemy, and puts both files into the mod. `--story
pre|post|any` changes when it exists; `--dry-run` shows the plan. Two
encounters in one mod stack. **In game: untested** -- please report what you
see.

**Editing a group directly.** `open`/`extract` a group list
(`scr/st100/etc/st100_e.gpl`) to edit it as YAML: `mUnitKindList` (the enemies
it can spawn -- the game loads an enemy's model because a group lists it),
`mSetCountMax` (its total; -1 = every placement once), the respawn type, the
story window (`mAppearBgn`/`mAppearEnd`; the Dragon fight is scenario 7800).
**Caveats, untested in game:** more than 3 unit kinds in a group needs the
community unit-expander plugin (see the enemy-limit chapter); and the ~10
on-screen enemy cap is an exe limit, not a value in these files. Riftstone
guarantees the files are well-formed; the game behaviour you find by playing.

---

## 9. Editing collision (.ocl)

Object collision is decoded. Every `.ocl` round-trips byte-exact; the collision
**primitives** (spheres/capsules — the shapes you tune for accuracy) are
editable:

```bat
Riftstone.cmd open collision/em/e01/e0100/e0100.ocl      # list every shape + position
Riftstone.cmd extract collision/em/e01/e0100/e0100.ocl --mod "My Mod"
```

```yaml
form: primitive
primitives:
  - offset: 20
    shape: capsule
    radius: 35.0
    position: [0.0, -30.0, 20.0]
    extent: 0.0
```

Change `radius`, `position`, `extent`, or `shape`; save; install. Only those
fields change — everything else in the file is preserved exactly. This is the
tool for the “stumbling on flat ground / invisible ridge / blocked doorway”
class of collision problems.

**Simple vs complex files:** 268 files are fully editable. 91 enemy-collision
files are a richer node tree: Riftstone exposes their hitbox primitives for
editing and preserves the rest byte-exact (so they're safe to save). 19 files
have no shape Riftstone recognises yet; they still round-trip exactly.

---

## 9b. Textures and reskins (.tex)

Textures are the most common mod of all: new armour colours, monster skins,
retextured weapons, custom UI. The game keeps them as `.tex` files (there are
73,706 of them). Riftstone converts a `.tex` to a standard `.dds` you can open
in **Photoshop, GIMP or Paint.NET** (with the free DDS plugin), and back again —
byte-for-byte, so nothing else in the file changes.

The whole workflow is three steps:

```bat
Riftstone.cmd extract skn\f\em\em0000\00\tex\em0000_00_BM.tex --mod "My Mod"
Riftstone.cmd tex to-dds "My Mod\...\em0000_00_BM.tex"     # makes em0000_00_BM.dds next to it
:: edit em0000_00_BM.dds in your image editor, save it back as the SAME format
Riftstone.cmd tex from-dds "My Mod\...\em0000_00_BM.dds"   # writes em0000_00_BM.tex back
```

`tex info` tells you what you're looking at before you start:

```
> Riftstone.cmd tex info em0000_00_BM.tex
  em0000_00_BM.tex: 1024x1024 texture, 11 mip level(s), format 24 (BC 16-byte block), revision 0x99  [exports to .dds]
```

Two rules that keep the round trip exact:

- **Keep the same compression when you save the `.dds`.** `to-dds` writes DXT1,
  DXT5, ATI2 or uncompressed to match the original; save it back the same way.
  `from-dds` uses the original `.tex` sitting next to the `.dds` as a template,
  so it restores the exact game format — keep the two files together, or point
  at the original with `--like`.
- **`_BM` is the base colour, `_NM` is the normal map, `_MM`/`_RM` are masks.**
  Normal maps are the `ATI2` (BC5) ones; don't paint them like a colour image.

Cube maps (the sky and reflection textures, names ending `_CM`) and one oddball
format can't become a `.dds`; edit those as raw `.tex` or leave them. `tex info`
says `[exports to .dds]` only for the ones that can.

**What's guaranteed and what isn't.** The conversion is proven byte-for-byte on
every texture in the game (`check_corpus --only tex`). Whether your *edited*
image looks right in game is up to your editing — Riftstone guarantees the
container, not your art.

---

## 10. Mods

A mod is a folder:

```
My Mod\
  riftstone-mod.json          name, version, author, priority
  files\                      a resource here replaces it in EVERY archive that has it
    param\em0100.shl.yaml
    collision\em\e01\e0100\e0100.ocl.yaml
  archives\                   change one archive only
    rom\enemy\em0100.arc\model\em\e01\e0100\e0100_BM.tex
```

- `Riftstone.cmd new "My Mod"` — make it in the mods folder (`--here` for the
  current folder, or give a path). Commands then find it by name.
- `Riftstone.cmd mods` — every mods folder, the mods in it, which are installed.
- `Riftstone.cmd build "My Mod"` — build to `My Mod\build\` without touching the
  game (to inspect the archives).
- `Riftstone.cmd install "My Mod"` — enable and apply. Originals are copied to
  `<game>\riftstone\vanilla` and verified first.
- `Riftstone.cmd watch "My Mod"` — rebuild and reinstall every time you save.
- `Riftstone.cmd uninstall "My Mod"` / `restore` — back out cleanly.
- When two mods change the same resource, the higher **priority** wins and
  Riftstone tells you.

---

## 11. The loader

An optional `dinput8.dll` that adds three things, all switchable in
`riftstone_loader.ini`:

```bat
Riftstone.cmd loader install     # or Studio > Game
```

- **Overlay** — serves your mods’ archives from `<game>\riftstone\overlay` so
  `nativePC` is never modified. With it, `watch` can update mods while the game
  runs.
- **Crash reports** — on a crash, writes `riftstone\logs\crash-*.txt` (+minidump)
  with the exception, registers, call stack, and the last files opened.
- **Plugins** — loads any `.asi`/`.dll` in `<game>\riftstone\plugins`
  (`Riftstone.cmd loader plugin add <file>`). An existing dinput8 (e.g. DDDA
  Tweak) keeps working — it’s chained.

---

## 12. Raising the enemy/unit limit

The GPL per-group unit-kind limit (vanilla 3) is raised by a native plugin,
vendored and verified byte-for-byte against your exe:

```bat
Riftstone.cmd loader install
Riftstone.cmd loader plugin add native\plugins\lod_tuner\out\lod_tuner.asi
```

Note the memory trade-off documented in
the third-party unit expander's notes (`vendor/unit_expander/README.md`, outside git): the array size is a big multiplier on
per-group memory, so keep it as low as your project needs.

---

## 13. Command reference

| Command | Does |
|---|---|
| `doctor [--verify]` | check the setup; `--verify` hashes every original archive |
| `open <path>...` | open any resource to the best view (YAML if editable, else a read-out) |
| `find <text> [--type ext]` | search by name: an enemy with the files that shape it (stats, attacks, AI), a room or stage, then every resource whose name holds the text |
| `param <file>...` | resource ↔ YAML (XFS params/FSM, `.ocl` collision, `.gmd` text) |
| `fsm <path>` | an AI state machine as readable states, actions and conditions |
| `tex info <file>` / `tex to-dds <file>` / `tex from-dds <file> [--like <orig.tex>]` | a texture's size/format/mips; convert `.tex` ↔ `.dds` to edit in any image tool |
| `spawns list <layout>` / `spawns copy <layout> <n> --at x,y,z --mod <m>` / `spawns remove <layout> <n> --mod <m>` | a layout's placements; add a copy; remove one |
| `items list [words]` / `items new <name> --like <item> --mod <m>` / `items shop <item> --shop <shop> --mod <m>` | browse items; add a new item in an unused slot; put an item on sale |
| `items recipe <a> <b> --makes <item> --count <n> --mod <m>` | a crafting recipe |
| `items sets <item>` / `items drop <item> --set <id> --percent <n> --mod <m>` | which drop sets hold an item; add an item to a drop set |
| `text find <words>` / `text add <file> "<line>" --mod <m>` | find any line of game text; add a line in every language |
| `unpack <arc>` / `pack <folder>` | archive ↔ editable folder |
| `extract <path>... --mod <m>` | copy originals into a mod (as YAML where possible) |
| `new <name>` | create a mod in the mods folder (`--here`: the current folder; a path: there) |
| `mods` | your mods: where they are, which are installed |
| `build <mod>` / `install [mod]` / `uninstall <mod>` / `restore` | build & apply mods |
| `watch [mod]` | reinstall on every save |
| `status` / `index` | what’s installed / resource index |
| `loader status\|install\|remove\|plugin …` | the loader and its plugins |
| `studio` | the browser app |

---

## 14. Status

| Area | State |
|---|---|
| Archives (`.arc`) | **Done** — 8,536/8,536 byte-exact |
| XFS params, FSM, SHL, SCE (44 types) | **Done** — 4,601/4,601 byte-exact YAML |
| FSM readable view | **Done** — 3,073/3,073 decompile; operators measured on the game |
| Text `.gmd` (dialogue, items, menus) | **Done** — 7,954/7,954 byte-exact; add a line in all 7 languages |
| Spawn/object layouts `.lot` | **Done (data)** — decoded completely (the exe's grammar, 74 classes); 6,209/6,209 byte-exact; all 48,424 records, every field; add/remove in every layout; Studio map with drag, Duplicate, Remove; in game **untested** |
| World map + encounters | **Done (data)** — `riftstone world` (stages, groups, layouts, enemies); `riftstone encounter` (N of an enemy as a new horde group); in game **untested** |
| Custom items (item list + text + shops + recipes + drops) | **Done** for consumables/materials — item list, drop tables and recipes byte-exact; 175 free slots; `items new/shop/recipe/drop`; in-game **untested** |
| Collision `.ocl` | **Done (simple) / partial (enemy)** — 378/378 byte-exact, 6,055 shapes editable |
| Textures `.tex` | **Done** — 11,221/11,221 byte-exact; 11,199 flat textures `.tex ↔ .dds` byte-exact; edit in any image tool; in game **untested** |
| Loader (overlay, crash logs, plugins) | **Done** — harness-tested; in-game read-out via `loader.log` |
| Enemy/unit GPL limit | **Verified + built** `.asi`; confirm in game |
| Robustness | Every entry point fuzzed; findings fixed with regression tests |
| Layout record kinds that are not placements (1,290 layouts), equipment stat tables (`LvParam*.itemlv`), LMT animation, enemy-collision full decode | **In progress** |
| Which text file an FSM message order (type + quest) reads | **Not measured** — test in game |
| New stages, 10-on-screen cap, exe item properties | **Need the exe side** (Chris’s tools / Arcs.dll / PS3 build) |

Full detail: `docs/formats.md` (measured format facts), `docs/roadmap.md`,
`docs/issues-triage.md` (every issue mapped).

---

## 15. Troubleshooting

- **“Dragon’s Dogma was not found.”** Pass `--game "…\DDDA"` or set
  `RIFTSTONE_GAME`.
- **“…is in use by the game.”** Close the game (direct mode), or install the
  loader to edit while it runs.
- **An edit didn’t take effect in game.** The file is valid (Riftstone checks
  that), but a value’s in-game *meaning* is discovered by testing — Riftstone
  guarantees well-formed files, not game behaviour.
- **Something looks wrong.** `Riftstone.cmd restore` returns every changed
  archive to the original, verified. Your mod projects are untouched.
- **A crash.** With the loader installed, read the newest
  `<game>\riftstone\logs\crash-*.txt`.

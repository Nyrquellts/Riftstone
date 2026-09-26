# The world, mapped: stages, groups, layouts, placements

How Dragon's Dogma: Dark Arisen decides **what stands where**, measured on Steam build 2364871 from
the retail `DDDA.exe` (loaders, tables and name formats, with addresses) and proved on every file in
the game. This is the map behind `riftstone world` and `riftstone encounter`: with it, "put 100
goblins in the Proving Grounds" is a question the files answer, not a guess.

Status key as elsewhere: a claim here is **measured** (the exe code or the whole corpus says so) unless
it says UNKNOWN. Nothing below has been observed in the running game; "the file is valid" is not "the
edit works in game".

## The model

```
stage S
 ├─ scr\st<S>\etc\st<S>_e.gpl   enemy groups           ─┐  a group list (rLayoutGroupParamList):
 ├─ scr\st<S>\etc\st<S>_n.gpl   NPC / hostile humans    │  groups numbered 0..294, each with unit
 ├─ scr\st<S>\etc\st<S>_p.gpl   objects                 │  kinds, spawn cap, respawn, story window,
 ├─ scr\st<S>\etc\st<S>_t.gpl   AI sensor targets      ─┘  hours, areas (hit / life / kill shapes)
 │     └─ group N ── scr\st<S>\etc\st<S>_<X>m<Z>n_<t>N.lot   its placements in map cell (X, Z)
 ├─ scr\st<S>\etc\st<S>_<X>m<Z>n_s00.lot                  a cell's static models (belong to no group)
 └─ scr\st<S>\etc\st<S>.spn  ── id/DDN/message/common/map_placelist   the stage's room names
placement mName        ── the unit it creates (em0100 a goblin, om1030 an object, em1000 a bandit)
enemy mEmItemTable     ── -1: the enemy's own drops; else a set of etc/item/ItemEmListSetTbl
object mSetTableID     ── a set of etc/item/itemSetTbl (chests, gathering spots); mSetItemNo: one item
enemy id               ── id/DDN/message/common/enemy_name_<lang> (its labels carry the ids)
archive A              ── an rArchive "ARCS" resource named rom\X inside A: A pulls in archive rom/X
                          (enemies -> their shell archives, stage packs -> object archives; formats.md)
```

In numbers (`riftstone world`, this game): 56 stages, 199 group lists, 3,925 groups, 6,342 layout
files (6,209 distinct), 48,424 records in the distinct files, 109 enemies.

## How the engine finds each piece (measured)

**A group's layout file is named by the engine, not listed.** `DDDA.exe` builds
`scr\st%03d\etc\st%03d_%02dm%02dn%s%02d` (format string at `0x01562268`, used at `0x004A3808`) from
the current stage, the cell, the type prefix and the group's number (`group & 0x1FF`). The prefixes,
in the engine's table at `0x0163AF30`, are `_s _p _e _n _t` (nLayout type 0..4). rLayout's own path
parsers (`filePath2LayoutID` `0x00CC1A00`, `filePath2SplitID` `0x00CC1BC0`) read the same name back
into (type, number, stage) and (cell X, cell Z).

Proof on the corpus: **every** `_e` layout (1,419 of 1,419), `_p` (2,674 of 2,674) and `_t` (1,280 of
1,280) names a group that exists in its stage's list, and 643 of 648 `_n` layouts do (the other 5 are
st100 NPC files for groups the list does not have). The 321 `_s00` files have no group list.

**A group's number is a slot in a 295-entry table.** `mGroupList` in every `.gpl` has exactly 295
entries (`cArray<cOmGroupData,295>`); entry N is `0x80000000` when group N exists, else 0 -- 3,925
marked entries for 3,925 groups. The group record's own `mGroup` field is 9 bits, but the table caps
group numbers at **0..294**. The busiest list, `st100_e`, uses 292 of them.

**`mLayoutIDArray` is the group's cells, not its files.** Every one of its 6,018 entries holds the
stage id as `mLayoutID` and the owning group's number as `mGroup`; `mSplitX`/`mSplitZ` list the map
cells the group spans. A cell listed there need not have a layout file (3,244 of 6,012 do).

**Layouts stream with their cell.** The same layout resource sits in several archives: for the open
field (st100) each cell's layouts are in `rom/stage/stage100/lot/m<X0>/st100_<X>m<Z>n_lot`,
`split/m../n../st100_<X>m<Z>n` and `split_sub/m../n../st100_<X>m<Z>n_sub` (252 layout keys live in
more than one archive); other stages keep them in the stage archive (`rom/stage/stage400/stage424`),
and the DLC stages 443/444 also in `rom/dl1/stage/...`. A new layout goes into every archive that
holds the stage's layouts for its cell.

**Enemy models load per unit, through `cLayoutSetEnemy::addArcLoadTbl`** (PS3 `0x0046932C`, called
from `cLayoutSetEnemy::move`), which starts a tagged archive load. Its parameter is a
`cLayoutSetCharaBase::cUnitData` (the PS3 mangled name says so; an earlier note here said
`cGroupParam::cUnitData`): the layout set's own unit entry, not the `.gpl` entry itself. That the
entry's enemy comes from the group's unit list (`mUnitKindList`) is the working assumption, and
`riftstone encounter` lists the new enemy there, but the copy from the group into the layout set
has not been traced. So whether an enemy a stage never had loads its model: UNKNOWN until played.
An enemy archive's own `ARCS` references name its projectile archive (`riftstone world deps
rom/enemy/em0202` -> `rom/shell/shellhellhound`, and the same for `em0200`, `em0201`, `em0203`); none of
the game's 9,990 archive references names an enemy archive.

**Room names.** `rStagePlaceName` (`.spn`) place ids index `map_placelist` (see `docs/stage-map.md`).

**Enemy names.** `id/DDN/message/common/enemy_name_<lang>` has 112 names whose labels carry the ids
(`e0100_goblin`, `em0103_`). Three families are labelled with older design ids than their archives;
the archives say which (their resources carry the old ids: `em2001` holds `e0301` models, `em2002`
`e0302`, `em2100` `e0800`, `em2101` `e0801`, `em6000` `e5700`, `em6001` `e5701`; `em2003`, the archers,
reuses `e0300`/`e0301`), so Riftstone maps e0300..0303 -> em2000..2003, e0800/0801 -> em2100/2101 and
e5700/5701 -> em6000/6001. Names are read from the player's own game at run time; Riftstone ships none.

## The layout format (`.lot`, rLayout v16), complete

`src/riftstone/lot.py` implements it; `tools/check_corpus.py --only lot` proves it (all 6,209
distinct layouts byte-for-byte, binary and YAML; copy-then-remove restores every one).

```
"lot\0"  u32 version = 16  u32 record count          rLayout::load              0x00CC1790
record:  s32 id  u32 kind (< 75)                      rLayout::SetInfo::load     0x00CC1F60
         then the fields of the class the kind names  nLayout set-info table     0x017EE988 (74 classes)
```

A class's loader reads its own fields and then calls its parent's, so a record is the most derived
class's fields first and `cSetInfoCoord`'s (name, position, angle, scale) **last** -- which is why a
45-byte transform block ends every placement. Types: integers `u8 s16 u16 s32 u32`, `f32`, `v3` (3 x
f32), `str` (NUL-terminated; `MtDataReader::readString` `0x00CFEE30`). Field names are the engine's,
from each class's `createProperty`; `lot.GLOSS` translates the developers' Japanese ones.

Kinds 0..54 are the PS3 build's table; 55..73 were added for Dark Arisen (the BBI enemies). Kind 0
(`cSetInfo`) is abstract and unused; 55 repeats `cSetInfoEnemy0102`.

| Kind | Class | Then | Own fields, in file order |
|---|---|---|---|
| 1 | `cSetInfoCoord` |  | `mSetID` s32, `mName` str, `mOrder` u32, `mPosition` v3, `mAngle` v3, `mScale` v3, `mDrawDistance` f32, `mIsOnSplitAreaIgnore` u8 |
| 2 | `cSetInfoPawn` | cSetInfoCoord | `mAreaHitNo` s32, `HP倍率設定の有無` u8, `HPの倍率` f32, `攻撃力倍率設定の有無` u8, `攻撃力の変化倍率` f32, `防御力倍率設定の有無` u8, `防御力の変化倍率` f32, `魔法力倍率設定の有無` u8, `魔法力の変化倍率` f32, `魔法防御力倍率設定の有無` u8, `魔法防御力の変化倍率` f32, `mAIKnowledgeFlag` u32, `mAIKind` u32, `mIsAINoTarget` u8, `mIsUseItemEveryone` u8, `mIsDragonBallDrop` u8 |
| 3 | `cSetInfoEnemy` | cSetInfoPawn | `mLifePointGroup` u32, `mUseFirstSetThinkTbl` u8, `mStartTableNo` s32, `mBossFlag` u8, `mBgmBossFlag` u8, `mEmItemFlag` s32, `mEmItemTable` s32, `mDieSet` u32, `死体速攻消滅フラグ` u8, `死亡消滅時間延長` u8, `mRandomSetIgnore` u8, `mExperienceOW` u32, `mFsmFilePath` str, `センサー半径倍率を使用する` u8, `センサー半径倍率` f32 |
| 4, 69 | `cSetInfoEnemy0100`, `0103` | cSetInfoEnemy | `mEquipType` u32, `mIsLeader` u8, `mIsWeaponHandStart` u8, `mIsBallistaLicense` u8 (0103 adds `mType` s32) |
| 5, 6, 55 | `cSetInfoEnemy0101`, `0102` | cSetInfoEnemy | `mEquipType` u32, `mIsLeader` u8, `mIsWeaponHandStart` u8 |
| 7, 8, 9, 56, 57 | `cSetInfoEnemy0200`..`0204` | cSetInfoEnemy | `mWolfType` u32, `mIsRandamScale` u8, `mWolfScale` f32 |
| 10..13, 58..62 | `cSetInfoEnemy0400`..`0408` | cSetInfoEnemy | `壁スタート` u8 (starts on a wall), `光学迷彩` u8 (camouflaged) |
| 14..16, 18, 22, 63..65 | `cSetInfoEnemy0500`..`0507`, `2000` | cSetInfoEnemy | `mPartsVariationNo` u32 |
| 17 | `cSetInfoEnemy0503` | cSetInfoEnemy | `mEquipType` s32, `mPartsVariationNo` u32 |
| 19 | `cSetInfoEnemy0600` | cSetInfoEnemy | `mHoverMainMode` u8, `mbOmBreakCheck` u8, `mOmBreakGroup` u32, `mOmBreakID` u32, `mHoverActionStart` u8 |
| 20 | `cSetInfoEnemy0700` | cSetInfoEnemy | `mPowerUpLv` u32 |
| 23 | `cSetInfoEnemy5000` | cSetInfoEnemy | `mType` u32, `mWeaponType` u32, `mTuskType` u32, `西部サイクロ` u8, `覚者の証サイクロ` u8, `覚者の証サイクロのときの経験値` u32, `…残りHPの倍率` f32, `…足ののけぞり値` f32, `…足のぶっとび値` f32 |
| 24 | `cSetInfoEnemy5100` | cSetInfoEnemy | `mInitMediumBreak` u32 |
| 25 | `cSetInfoEnemy5101` | cSetInfoEnemy | `mGolemNo` u32 |
| 26 | `cSetInfoEnemy5101_00` | cSetInfoEnemy | `mGolemNo` u32, `mMediumRegionNo` u32 |
| 27 | `cSetInfoEnemy5200` | cSetInfoEnemy | `死亡演出カメラをする` u8 (death camera) |
| 28 | `cSetInfoEnemy5300` | cSetInfoEnemy | `mStartWaitTime` f32 |
| 30, 70 | `cSetInfoEnemy5500B`, `5500C` | cSetInfoEnemy | `触手が魔法出来る数` u32, `触手がワープ出来る数` u32, `触手がワープ出来る範囲` v3 |
| 32 | `cSetInfoEnemy5800` | cSetInfoEnemy | `mSeqButtleSts` u32 |
| 33, 67 | `cSetInfoEnemy5801`, `7000` | cSetInfoEnemy | `mIsDieSet` u8 |
| 34 | `cSetInfoEnemy5900` | cSetInfoEnemy | `mSetType` u32, `飛ばない` u8, `固有喋り` u8, `リッチひょうい` u8, `旋回飛行しない` u8 |
| 35 | `cSetInfoEnemy6000` | cSetInfoEnemy | `死神特別設置か？` u8, `死神特別設置フレーム` f32 |
| 45 | `cSetInfoEnemy9807` | cSetInfoEnemy | `動作時間(秒)` f32, `サウンドOFF` u8 |
| 66 | `cSetInfoEnemy5001` | cSetInfoEnemy | `mType` u32, `mIsWeaponInitRemove` u8, `mWeaponInitPos` v3, `mWeaponInitAngle` v3 |
| 21, 29, 31, 36..44, 68, 71..73 | `cSetInfoEnemy0900`, `5500`, `5501`, `8000`..`9000`, `9100`, `7001`, `5400`, `5401` | cSetInfoEnemy | (none) |
| 46 | `cSetInfoInsModel` | cSetInfoPawn | `mLightGroup` u32, `mOverwriteLightGroup` u8, `mTransMode` u32, `mOverwriteTransMode` u8 |
| 47 | `cSetInfoNpc` | cSetInfoPawn | `mNpcPriority` s32, `FSMPath` str, `mNpcId` s32, `mScrAdjustOff` u8, `mObjAdjustOff` u8, `mPriority` s32, `mGoodsOff` u8, `mSimpleModelType` s32, `mPartsVariationNo` u32, `mClothType` u32, `mUseMouseJoint` u8, `mHumanEnemyKind` u32, `mHumanEnemyID` u32, `mRank` u32, `mMultiNpcKind` u32, `mUse24Schedule` u8, `mIsHumanEnemyLeader` u8, `mExperienceOW` u32, `mpTarget` sensor, `外部指定` u8, `待機行動タイプ` s32, `内部フラグ` s32, `待機行動番号` s32, `基本待機時間` f32, `ランダム待機時間` f32, `しぐさ行動番号` s32, `基本しぐさ時間` f32, `ランダムしぐさ時間` f32 |
| 48 | `cSetInfoOmModel` | cSetInfoPawn | `mSetTableID` s16, `mSetTableIDNight` s16, `mSetItemNo` s16, `mSetItemNoNight` s16, `mStartHour` s32, `mEndHour` s32, `mFree00` u32, `mFree01` u32, `mAngleType` u32, `mFlag` u32 |
| 49 | `cSetInfoOmFSM` | cSetInfoOmModel | `FSMPath` str |
| 50 | `cSetInfoOmFsmPlusSM` | cSetInfoOmModel | `mSeGroupId` s16, `mSePriority` s16, `mMapIconType` s16, `FSMPath` str |
| 51 | `cSetInfoOmPlusAng` | cSetInfoOmModel | `mCheckAngle` f32, `mCheckRange` f32 |
| 52 | `cSetInfoOmPlusSM` | cSetInfoOmModel | `mSeGroupId` s16, `mSePriority` s16, `mMapIconType` s16 |
| 53 | `cSetInfoOmDoorMsg` | cSetInfoOmModel | `mLockMsgNo` s16, `mOutMsgNo` s16, `mOpenMsgNo` s16, `mCheckAngle` f32, `mCheckRange` f32, then `mSeGroupId`, `mSePriority`, `mMapIconType` s16 |
| 54 | `cSetInfoSensorTarget` | (not a placement) | `mpTarget` sensor |

A `sensor` (`mpTarget`) is a u32 "present" flag, then -- when non-zero -- the class name and that
cAISensorTarget subclass's fields (loaded by name, `0x007A39A0`): `cAISensorTargetGeneralPoint`,
`...Npc`, `...StageAction` (two `s16` lists of at most 16 each), `...Unit`, each ending with the base
class's `mTypeBit mSndLv mStatusFlag mPos mDir mRange mObjectID mAttr mSphereRange mJntNo`. The
object sub-params are `cOmSeMapParam` (3 x s16), `cOmDoorMsgParam` (3 x s16, 2 x f32) and
`cOmAngleParam` (2 x f32).

### Limits the engine imposes (enforced by Riftstone)

| Limit | Why | Vanilla |
|---|---|---|
| record ids 0..1023, unique | the loader fills a 1024-entry id -> index table (`0x00CC18B7`); an id past it writes outside the table | ids 0..350; one file repeats an id |
| `mFsmFilePath` <= 63 bytes | read into a 64-byte buffer | longest 50 |
| other strings <= 255 bytes | read into 256-byte buffers | longest 54 |
| `mActParamIdx`, `mActParamQuestNo` <= 16 entries | copied into 16-entry buffers, unchecked | 16 |
| group numbers 0..294 | the 295-slot group table | up to 291 |
| records per layout | the id table's index is a byte, so more than 256 records alias (the vanilla statics files hold up to 351) | enemy files <= 31, objects <= 32, NPCs <= 40 |

## What the fields say (measured on the corpus)

* **Spawn caps and hordes.** A group with `mSetCountMax` >= 0 uses respawn type 5 in every enemy list
  but one case. Stage 330's group 35 (goblins and hobgoblins) has a cap of **100** and **7**
  placements; stage 706's group 3 (the Proving Grounds' goblins) a cap of **50** and **6**. Groups with
  fewer than their placements (stage 100's group 104: cap 4, 30 placements) pick among them. The cap is
  the group's **total** output and the placements are its spawn points: the game's own horde
  mechanism. 1,147 of 1,169 enemy groups have no cap (-1: every placement, once).
* **Respawn types** in enemy lists: 1 (767 groups), 0 (330), 3 (29), 5 (21, the capped hordes), 4 (19),
  2 (5), 6 (4). `mRspnDay` is the day count for the timed types. The kill record (`docs/enemy-waves.md`, PS3
  `sSetManager::registerEmData`): type 3 keeps none, a type-5 horde marks no kills, and type 2 keeps the group's lot
  flag with it and loses it when that flag goes off; the rest of what 2, 3, 4 and 6 do: UNKNOWN.
* **"Appears after a group is cleared"** (`mSetCondition.mIsEmGroupLink` + `mLinkEmGroup`) is used by 9
  groups, **all in NPC and object lists**, each naming a group of the same stage's *enemy* list: the NPCs
  or objects appear once those enemies are dealt with (st100 `_n` 121, 122, 123, 126, 127; st443/444 `_p`). No enemy
  group uses it; whether an enemy group honours it: UNKNOWN. (Earlier notes read these as enemy waves.) Enemy
  waves are state machines and lot flags instead: `docs/enemy-waves.md`, `riftstone waves`.
* **Scenario windows**: `mAppearBgn`/`mAppearEnd`; the Dragon fight is scenario 7800 (pre: `..7799`,
  post: `7800..`); 0/0 is always.
* **Per-placement stat multipliers** (`cSetInfoPawn`): HP, attack, defence, magick and magick defence,
  each with an on/off byte. An "elite goblin" is a placement edit.
* **Drops**: `mEmItemTable` is -1 on 5,500 of 5,525 enemy placements (the enemy's own drops); the other
  25 name a set of `ItemEmListSetTbl` for that placement only. Which set an enemy's own drops use: set by
  enemy type in the exe, UNKNOWN which. `mExperienceOW` overrides EXP (0: the enemy's own).
* **Objects' loot**: `mSetTableID` / `mSetTableIDNight` name `itemSetTbl` sets (1,595 of 1,596 used ids
  exist there); `mSetItemNo` / `mSetItemNoNight` give a single item instead.
* **Hostile humans** (bandits, soldiers) are `cSetInfoNpc` records with `mHumanEnemyKind` /
  `mHumanEnemyID` / `mRank`, and they stand in *enemy* layouts (555 of them) as well as NPC ones.

## Using the map

```bat
Riftstone.cmd world                        :: build or load the map; the overview
Riftstone.cmd world stages                 :: every stage: groups per list, placements, rooms
Riftstone.cmd world stage 706              :: its enemy groups: units, spawn caps, story windows, where they stand
Riftstone.cmd world enemy goblin           :: every layout that places goblins, with its group's settings
Riftstone.cmd world group 706 e 3          :: one group: settings, its layout files (and archives), every placement
Riftstone.cmd world deps em0200            :: archive references: what an archive pulls in, who pulls it in
Riftstone.cmd world types                  :: all resource types in the game and how Riftstone handles each
Riftstone.cmd waves 320 --flags            :: a stage's lot flags: who sets, clears or reads each, and the free ones
Riftstone.cmd spawns list scr/st706/etc/st706_00m00n_e03.lot
Riftstone.cmd open scr/st706/etc/st706_00m00n_e03.lot       :: every field of every record as YAML
```

The map is built from the installed game (about 3 seconds) and cached under
`%LOCALAPPDATA%\Riftstone\world-*.json`; it rebuilds when the game's archives change.

**In Studio**, the **World** tab is the same map made visual: the stage list (filter by id or room),
every placement of a stage on a zoomable top-down map (enemies, NPCs and hostile humans, objects,
sensor targets and statics, each switchable), the enemy groups with their settings (click one to
light up its placements), a placement inspector (click a dot: what it is, its layout and record,
"Open layout"), "where does this enemy spawn" across all stages, and the encounter form (click the
ground or a group, pick the enemy and the count, **Plan** shows the spawn points, **Write to mod**
writes the group and layout).

## 100 goblins

```bat
Riftstone.cmd new "Goblin Horde"
Riftstone.cmd encounter 706 goblin --count 100 --at group:2 --mod "Goblin Horde"
Riftstone.cmd install "Goblin Horde"
```

`encounter` (`src/riftstone/encounter.py`) builds the horde the way stage 330's group 35 is built:

1. **The template group** is the one whose enemies stand nearest the spot (`--at x,y,z` or
   `--at group:N`). Its areas, story window, hours and load flags are the ones that work there; a copy
   of it gets the first free number (0..294) and its `mGroupList` slot.
2. **The copy** lists only your enemy as its unit kind (so its archive loads), and -- for more enemies
   than spawn points -- gets the horde settings: `mSetCountMax` = the total, respawn type 5.
3. **Its layout** `st<S>_<X>m<Z>n_e<N>.lot` is written in the template's cell: `--at-once` spawn points
   (default 10, the enemy pool) on rings `--spread` apart, each a copy of a vanilla placement of the
   same enemy -- its class, equipment, AI script and settings.
4. **The mod** gets the group list under `files/` (it replaces the stage's in every archive that holds
   it) and the layout under `archives/<archive>.arc/` for every archive holding the stage's layouts for
   that cell. `build`/`install` verify both like any mod.

Encounters stack: a second one in the same mod reads the mod's group list and takes the next free
number. `--story any|pre|post` changes when the group exists; `--dry-run` shows the plan only.
`--like N` copies group N's conditions instead of the nearest group's (e.g. its lot flag, so the new group appears
exactly when group N does); `--skin N` makes every placement wear an enemy skin (`docs/enemy-skins.md`). A new
group's number is free in **every** enemy group list of the stage: stage 443 also has the DLC's `st443_e_dlc01`
(groups 20-22), and a layout name carries only the number. The group copied may be in either list (an encounter
among Everfall's DLC groups copies one of them, conditions and all); the copy goes into `st443_e`. In game UNKNOWN.

**Hours.** A group also holds a window of hours, `mDataSetHour.mSetHourBgn` / `mSetHourEnd`. Across the game's
3,925 groups (world cache, 2026-09-25): 3,673 hold 0..23, 87 hold 4..20 and 85 hold 20..4 (day and night pairs),
30 hold 0..0, and the rest are day/night variants such as 3..20, 20..3, 4..19. `--hours 20,3` sets the new group's
window (it may wrap past midnight); without it the copied group's hours stay. Both ends are written as the game's
own groups hold them; whether the game counts the last hour, and what 0..0 means, is UNKNOWN until seen in game.

**Plans.** `Riftstone.cmd encounters plan.json --mod "My Mod"` does every encounter a plan lists, in order, each
written before the next is planned (so they stack as above); `--dry-run` shows them all as a real run makes them
(the same group numbers, the same refusals), writing them into a scratch copy of the mod's group lists that is
removed afterwards, so the mod is not touched. A plan is JSON in the
format `riftstone-encounters/1` (`src/riftstone/encounter_plan.py` checks every field first); NYR-Lang
(`<path>`) writes one from spawn rules over stage, hour and story, e.g.

```
[stage == 330] and ([hour >= 20] or [hour < 4]) and [story >= 7800] => E(100) x 100 | near = 35 | points = 10
```

```bat
<path> encounters.nyr --target riftstone -o encounters.json
Riftstone.cmd encounters encounters.json --mod "Goblin Horde"
```

**Waves.** `Riftstone.cmd waves 320 --after 11 --wave em2000:6 --wave em0501:4 --mod "My Mod"` writes groups that
appear one after another, each once the group before it is dead (`docs/enemy-waves.md`): every wave is an encounter
copied from the `--after` group and gated on a lot flag nothing else in the game uses (respawn type 2), and one state
machine in the stage's own archive (`scr\st<S>\fsm\fix_nosave\`, which the game starts with the stage) waits for
each group's placements to die, sets the next flag, and clears them all after the last. In game UNKNOWN.

**On screen at once:** about 10 (sSetManager's enemy pool, `docs/re-enemy-cap.md`). A horde refills its
spawn points up to the total; more simultaneous enemies need the native pool raise.
**In game:** UNKNOWN until played. The group and layout are valid, verified by `build`, and use only
mechanisms the game's own groups use (the unit list that loads the model, a type-5 capped group, the
engine's own layout name); stage 100 has only 3 free enemy group numbers, so there `spawns copy` into
an existing group's layout is the other route.

## Still UNKNOWN

* The open field's cell grid: layouts' cells do not follow one world-coordinate grid (some groups'
  placements lie far outside their cell), so `encounter` uses the cell of the nearest existing enemy.
* What respawn types 2, 3, 4 and 6 do; what `isBelong` in a unit-kind entry means (Riftstone copies the
  value the game uses most for that enemy).
* Anything in game: that a new group spawns, that its enemies act inside the template group's areas,
  and how the pool handles a refilling horde.

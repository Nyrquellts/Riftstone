# Enemy waves: the next group appears when the one before it is dead

A brief asked to "patch the event dispatcher so cleared enemy groups can trigger successive waves", on the claim
that the engine links "trigger next wave on clear" only to NPCs and objects. The second half is right and beside
the point: `mSetCondition.mIsEmGroupLink` is used by 9 NPC and object groups only (`docs/world-map.md`). The game
chains enemy groups another way, in data: a state machine waits for a group to die and then sets a lot flag that
lets the next group load (`quest/q0012_b00`, `docs/fsm-grigori-and-waves.md`). **No native patch is needed.** What
was missing is a way for a mod to write such a chain; this page is the evidence behind `riftstone waves`, which
does.

Measured on 2026-09-26 against the installed game (Steam build 2364871) and its data. Addresses are `DDDA.exe`
unless marked PS3 (names from the PS3 build).
Byte claims in backticks are re-checked against the exe by `tools/doc_claims.py`. **Nothing here has been played:
every in-game effect is UNKNOWN.**

## Which state machines run in a stage

- **The stage's own machines are found, not listed.** `aStage::init` calls `aStage::setStageFSM` (PS3 name;
  `0x005081A0`): `8B C3 E8 4A 4E 00 00` at `0x0050334F`. It finds the stage's place in the game's 65-stage table
  (`64 00 C8 00 D2 00 DC 00` at `0x01530348`, stages 100, 200, 210, 220 ...; the lookup is
  `66 39 0C 45 48 03 53 01` at `0x005081F0`), takes that place's archive tag (5 + the place:
  `8B 04 85 9C 92 8D 01` at `0x00508203`, `docs/archive-tags.md`), and walks every resource of that loaded archive.
  For all 65 stages the tag's archive is `rom\stage\stage<H00>\stage<S>` (read from the tag table).
- Of those resources it takes the ones whose extension is `"fsm"` at `0x0156CF5C` (`BF 5C CF 56 01` at
  `0x00508269`) and whose path contains `"fsm\\fix"` at `0x0156D5E0` (`strstr`:
  `68 E0 D5 56 01 55 32 DB E8 B3 62 DE 00` at `0x005082A0`), which also matches `fsm\fix_nosave\`, and starts each
  as an FSM task: `68 E8 03 00 00 55 E8 75 FD FF FF` at `0x00508360` (PS3: `cFSMTaskCtrl::addFSMTaskFromName`,
  which refuses a name already running and gives each task its own `cFSMOrder`). The same walk starts
  `"event\\st%03d\\stage_FSM\\stage_%03d"` at `0x0156D5E8` (stages 000..005 by name).
- **Not in stages 800 to 804.** `aStage::setStageFSM` returns before the walk for them:
  `3D 20 03 00 00 0F 84 F3 01 00 00` at `0x005081AF` (stage 800) through `3D 24 03 00 00 0F 84 C7 01 00 00` at
  `0x005081DB` (804), each a jump to its epilogue `5F 5E 5D 5B 83 C4 54 C3` at `0x005083AD`. None of their
  archives holds a machine (two carry the staff roll's music); `riftstone waves` refuses those stages.
- In the game: 285 machines live under `scr\st<S>\fsm\fix\` (159) and `fix_nosave\` (126); all 285 sit in their
  stage's tag archive, 280 of them owned by `cFSMOrder`. Quest machines (`quest\qNNNN_bNN`) are started by the quest
  system and brought back from the save (PS3 `sSave::playerGameData::ToGameStageInit` -> `aStage::addFSMFromName`);
  scenario areas (`cScenarioArg_BootFSM`) start others.
- **So a mod can add a machine:** a new `.fsm` under `scr\st<S>\fsm\fix_nosave\` in `rom/stage/stage<H00>/stage<S>`
  is started with the stage (any stage but 800 to 804), like the game's 126. It needs no list and changes no
  machine the game ships.

## Saved or not

- The save keeps a task unless its path contains `"stage_005"` at `0x01561488` or `"nosave"` at `0x01561480`, as
  `68 88 14 56 01 56 E8 35 A1 E5 00 68 80 14 56 01 56` at `0x00494420` tests.
- It keeps **50**: the work data (`8D 8F D4 57 0B 00` at `0x004943C3`, 0x54 bytes each), the paths
  (`8D 97 3C 68 0B 00` at `0x004943BD`, 0x24 each) and each order's data (`8D 8F 44 6F 0B 00` at `0x004943B3`, 0x11C
  each) lie 0x1068, 0x708 and 0x3778 bytes apart, 50 records each (PS3 build: `cSAVE_DATA_FIX` holds
  `mFsmWorkData[50]`, `mFsmPath[50]`, `mFsmParam[50]`), and the count is stored after the loop
  (`89 87 BC A6 0B 00` at `0x00494506`). The loop adds one record per task, with no bound:
  `FF 44 24 18 83 44 24 10 54 83 44 24 14 24 81 C6 1C 01 00 00` at `0x004944D2`. The game's busiest stage runs 17
  saved `fix` machines (st380) plus its stage machines and the quests running; what more than 50 does to a save is
  UNKNOWN. A chain therefore lives in `fix_nosave`: it takes none of the 50, and starts over each time the stage
  starts.
- (PS3) Loading a stage, a saved task that is already running (a `fix` machine the stage just started) takes its
  saved state back (`cAIFSM::importWorkData`); a `fix` machine that is not running is skipped with "search FSM
  Error"; any other saved task is started again.

## Lot flags

- **A lot flag is a bit of one stage, kept in the save.** `cSAVE_DATA_STAGE.mStgFlag` holds 65 `cSTAGE_FLAG`
  (`C7 44 24 24 41 00 00 00` at `0x00758297`, `"mStgFlag"` at `0x0159136C`); each one's first four words are the
  stage's 128 lot flags, saved as `"LotFlag"` at `0x015912EC` / `"Flag000"` at `0x015912F4` ..Flag096
  (`C7 44 24 14 EC 12 59 01` at `0x00757C2A`). Stage 100 has 128 more, `"mLotFlagField"` at `0x0159135C`
  (`C7 44 24 24 04 00 00 00` at `0x00758251`).
- `sGameSys::setLotFlagOn` (`0x00443CA0`) takes a place in the table under 65 and a flag under 128
  (`83 FF 41 73 1D 81 FE 80 00 00 00` at `0x00443CC4`; the word: `8D 84 8B E8 DE 0A 00` at `0x00443CDD`), and for
  stage 100 also 128..255 (`8D 84 8B 30 33 0B 00` at `0x00443D00`); any other flag is ignored.
  `setLotFlagOff` (`0x00443F20`) the same.
- **SetLayout** is the state action `cFSMOrder::stateUpdateSetLayout` (PS3 name; `0x005C5080`), registered under
  `"SetLayout"` at `0x0157A6E4` by `68 80 50 5C 00 68 70 07 99 01 68 E4 A6 57 01` at `0x005B86A7`. It looks
  `mStageNo` up in the 65-stage table (`0F B7 4A 06 33 C0 66 39 0C 45 48 03 53 01` at `0x005C50BA`; a stage not in
  it does nothing), then sets (`mActType` 0) or clears (1) flag `mFlagNo` of that stage:
  `0F B7 4A 08 56 E8 9F EB E7 FF` at `0x005C50F7`. So `mStageNo` is a stage number, one of the 65.
- **A group's gate reads the current stage.** `cGroupParam` (`0x00CC5710`): with `mLoadCondition.mLotFlag` the
  group loads only while flag `mDataLotFlag.mFlagNo` is set (`F6 C3 02 74 4F 0F B7 77 2C` at `0x00CC571C`), with
  `mLotFlag2` flag `mFlagNo2` as well (`F6 C3 08 74 1F 0F B7 77 2E` at `0x00CC574C`); the flag is read through the
  running stage's place (`8B 80 2C 07 00 00` at `0x0075C123`; under 128: `81 F9 80 00 00 00` at `0x0075C132`).
  A chain's `mStageNo` must therefore be the stage of the group it opens.
- Flags persist until something clears them: `q0012_b00` sets flag 35 of st320 and never clears it. Some stages
  clear their own (PS3: `aStage602/603/604::init` clear all 128 of theirs; `aStage611::final` clears stage 200's).

### Who else touches a lot flag

A chain needs flags nothing else sets, clears or reads. Group gates alone do not say that: of the (stage, flag)
pairs other sources name, 35 of the new-game table's, 6 of the quest tables', 48 of the SetLayout orders' and 8 of
the code's constants gate no group. `waves.census()` reads them all (about 15 s, cached beside the world map):

| Source | What it names | In the game |
|---|---|---|
| group lists (`_e _n _p _t`, DLC lists) | `mDataLotFlag.mFlagNo`/`mFlagNo2` with `mLoadCondition.mLotFlag`/`mLotFlag2` or `mDeleteCondition.mLotFlag` | 806 gated enemy groups (35 with both slots) |
| state machines | `SetLayout` (`mStageNo`, `mFlagNo`), `CheckLayout` (`mStageNo`, `mBitNo`); an AI machine's `LotFlag` (`FlagNo`) acts on the running stage (PS3 `cThinkFSM::stateUpdateLotFlag`), taken as the stage its path names | 228 machines with SetLayout, 22 CheckLayout, 55 LotFlag orders |
| quest tables `.qct` | results 6 (on) and 7 (off): `mParam00` stage, `mParam01` flag (PS3 `cQuestCtrl::QuestTblResultProc`) | 319 pairs, 311 gating a group of that stage |
| notice-board quests `etc\infQuest\Infquest.qif` | `mChkLotStage`/`mChkLotFlag` of each 91-byte record (+28/+30), set and cleared by quest results 0x42/0x43 (PS3) | 110 of 478 records |
| stage action params `.sap` | flag type 3 in `OnFlag`/`OffFlag`/`SetOnFlag`/`SetffFlag` | 1 |
| om4535 placements | `mFree00`, the flag `uOmObj4535` sets | 3 (stage 100: 191, 192) |
| the new-game table | 272 (stage, flag) set by `sGameSys::initFlagNewGame`, built on the stack from `0x00444840` and set by `0F B7 44 B4 14 0F B7 4C B4 16 52 E8 50 E3 FF FF` at `0x00445940`, 272 times (`81 FE 10 01 00 00` at `0x00445957`) | 272 |
| the code | stage 100's field flags 150..190 at random (`BF 96 00 00 00 8D 6C 24 1C 8D 59 29` at `0x0050A833`); on Bitterblack Isle a random variant flag 60..79 (`46 83 FE 50 72 E5` at `0x0052F935`), floor flags 100..103, 126 (stage 423: `B8 32 00 00 00 8D 48 4C` at `0x0052F645`) and 127; constants in the stages' own load/init/final, quest code and `cFSMOrder::updateCamera` (`waves.NATIVE`) | about 100 |

`riftstone waves <stage> --flags` prints a stage's flags with their users and the free ones. A chain takes the
highest free ones (the game numbers its own from 0 up), and on stage 100 only 0..127.

## How a machine waits for a group's death

- **EnemyCheck** is `cFSMOrder::stateCheckEnemy` (PS3 name; `0x005BEF10`), registered under `"EnemyCheck"` at
  `0x0157A5D4` by `68 10 EF 5B 00 53 68 70 07 99 01 68 D4 A5 57 01` at `0x005B7EEE`. It runs every frame the state
  runs. It counts the targets that pass (`0x005BF4D0`): only `cLinkUnit::cTarget` with `mType` 2, as
  (`mNo0` = group, `mNo1` = placement id): `83 7E 04 02 75 1D 8B 7E 0C 8B 4E 08` at `0x005BF522`. With
  `mCheckType` 0 all must pass (`8B 56 04 3B FD 72 06 0F B6 4E 0C` at `0x005BEF5F`): then `FreeFlag[mFlagNo]` is
  set to `mFlagBool`, else to its opposite; the count goes to `mResultNum`'s slot (`89 7C 83 74` at `0x005BEF85`).
  Type 1 is any.
- **The check** (`0x005BF3D0`; `83 F8 03 0F 87 D0 00 00 00 FF 24 85 B8 F4 5B 00` at `0x005BF3DA`) takes
  `EM_CHECK_TYPE` (PS3 build): 0 EXIST, 1 KILL, 2 ALIVE, 3 DEAD, 0x1000 USABLE (always). DEAD is "the set manager
  recorded its kill" (`8B 0D 04 A5 8F 01 57 8B C6 E8 B6 70 EE FF 84 C0 75 A2` at `0x005BF44C`) or "the unit found
  under (current stage | group << 10, id) has no health left" (`25 FF 03 00 00 51 C1 E6 0A 0B C6` at
  `0x005BF46E`). A group that has not spawned yet is not dead: `q0012_b00`'s `zomb2_ck` sets flag 35 and checks
  group 11 in the same state.
- **The kill record** is 16 bytes a group at `sSetManager + 0x18A90` (`8B B4 C1 98 8A 01 00` at `0x004A6516`),
  a placement's bit `1 << id` in the mask above bit 28 (`B8 01 00 00 00 D3 E0 0F AC FE 1C` at `0x004A653D`). An id
  past 31 wraps (x86 `shl` takes 5 bits), so a chain waits for ids 0..31. The game's own targets name record ids,
  not positions: st100 group 21's two layouts hold ids 1, 3 and 2, 4 and `q0063_rolandOsoware` checks 1..4; st380
  group 3 holds ids 1..5 and `set_02` checks 5; of 362 targets, 5 match an id and no position, none the other way.
  Every enemy group keeps its ids unique across its layouts (142 have several), and the largest id is 31.
- **Respawn types decide the record.** (PS3 `sSetManager::registerEmData`) type 3 keeps no record and a type-5
  horde counts instead of marking kills, so "all dead" is seen only while every body is still there; type 2 keeps
  the group's lot flag in its record, and `sSetManager::updateLotFlagMgrData` (`0x004A7D10`) erases a type-2 record
  whose flag is off (`0F AC CA 11 80 E2 07 C1 E9 11 80 FA 02` at `0x004A7D4A`); `setLotFlagOff` calls it
  (`8D 85 90 8A 01 00 E8 67 3D 06 00` at `0x00443F9E`). All five of the game's type-2 enemy groups are gated on a
  lot flag. The game's 72 DEAD checks target groups of types 0 (21), 1 (16), 4 (11) and 3 (4, one skeleton group,
  st370 group 40); none capped (`mSetCountMax`) and none with a random pattern (`Random_Division`,
  `Random_Pattern`), which would spawn only some placements.
- **The link.** The state's link condition is `FreeFlag[0] is set` (operator 1); with `mSetting` 1 the links are
  checked after the actions (`docs/formats.md`, `fsmcheck.py`), so a state's EnemyCheck has updated the flag for
  this frame before its link reads it. 125 EnemyCheck orders in 58 machines, 61 of them in `fix` and `fix_nosave`
  machines (`scr\st700\fsm\fix\q053_emck_first` waits for st700's group 5; `scr\st380\fsm\fix\set_02` sets flag 4
  and then waits for group 3).

## What `riftstone waves` writes

```bat
Riftstone.cmd waves 320 --flags                      :: stage 320's lot flags: who uses each, which are free
Riftstone.cmd waves 320 --after 11 --wave em2000:6 --wave em0501:4 --mod "Catacomb Gauntlet" --dry-run
Riftstone.cmd waves 320 --after 11 --wave em2000:6 --wave em0501:4 --mod "Catacomb Gauntlet"
```

`src/riftstone/waves.py`:

1. **Each wave is a new enemy group** planned by `encounter.plan`, a copy of the `--after` group (or `--like`):
   its areas, story window, hours and cell, standing where the `--after` group stands (or `--at`), every spawn
   point a copy of the enemy's ordinary vanilla setup. `rules/waves.nyr` then gates it on its own flag
   (`mLoadCondition.mLotFlag` 1, `mDataLotFlag.mFlagNo`), keeps the copied group's own flag as the second slot, sets
   respawn type 2, no cap, no random pattern, no delete condition. Its number is free in all of the stage's lists
   (DLC lists too).
2. **One machine per chain**, `scr\st<S>\fsm\fix_nosave\riftstone_waves_e<after>.fsm`, added to the stage's own
   archive: `wait_e<after>` (EnemyCheck DEAD, all placements) -> `open_<flag 1>` (SetLayout on) -> `wait_e<wave 1>` ->
   ... -> `wait_e<last wave>` -> `close` (SetLayout off, every flag of the chain) -> `finish`. Every state has
   `mSetting` 1; the machine is built in the classes and field order of the game's own and checked by `fsmcheck`
   (the game's transition rules) before it is written; the build checks it again (`mod.check_fsm`).
3. **Clearing at the end re-arms the chain:** the waves' type-2 kill records are erased, and the next time the stage
   starts the machine waits for the `--after` group again (which follows its own respawn rules; while it stays
   dead, wave 1 opens at once). A save in the middle keeps the flags set, so the wave that was up is up again.

Refused before anything is written: an `--after` group of respawn type 3 or 5, capped or with a random pattern; a
placement id past 31 or repeated; a `--like` group that needs something outside it to appear
(`mSetCondition.mFsm`, `mRequest`, `mSimpleEv`, `mChArea`), that already needs two lot flags, or that the mod added
(the waves copy a game group's areas and cell); a second chain after the same group in one mod, or a chain after a
group another chain of the mod opens (that chain clears the flag when it ends); a wave over 31 enemies; more than
16 waves; a stage with too few free flags. Every wave is planned in a scratch copy of the mod's group lists, so a
refusal at wave 3 leaves the mod as it was. Encounter plans (`encounter_plan.py`) take no `after`: a plan's
groups are numbered as it runs, and a chain needs all its waves at once.

## Still UNKNOWN

- That the game starts the new machine and the waves appear, fight and die as planned: the files use only paths
  the game's own `fix_nosave` machines and gated groups use, and nothing has been played.
- Whether a flag set by an earlier visit makes a wave load before the machine's first frame (a save made mid-chain
  keeps its flags; that is intended).
- What becomes of bodies of a wave when its flag is cleared at the end.
- An enemy new to a stage loading its model (as for `riftstone encounter`).
- Two mods changing the same stage's group list clash (the later one's wins whole), as for encounters.

## Repeat it

- The data: `riftstone fsm quest/q0012_b00.fsm`, `riftstone fsm scr/st380/fsm/fix/set_02.fsm`,
  `riftstone world group 320 e 11`, `riftstone waves 320 --flags`.
- The code: `tools/re_dd.py` at the addresses above; the PS3 build's names
  (`aStage::setStageFSM`, `cFSMTaskCtrl::addFSMTaskFromName`, `sSave::playerGameData::ToSave`,
  `cFSMOrder::stateCheckEnemy`, `cFSMOrder::checkEnemy`, `sSetManager::isKilledEnemy`,
  `sSetManager::registerEmData`, `sSetManager::updateLotFlagMgrData`, `sGameSys::setLotFlagOn`) and its types
  (`cSAVE_DATA_STAGE`, `cSTAGE_FLAG`, `cSAVE_DATA_FIX`, `EM_CHECK_TYPE`).
- The tool: `tests/test_waves.py` (a stand-in game, and st320 on the installed one), the fuzz target `waves`.

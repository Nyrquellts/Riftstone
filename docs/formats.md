# Formats, as measured

Everything here was measured on Steam build 2364871 (`DDDA.exe` SHA-256
`19facc26…77b8`, PE32 x86, large-address-aware). Each claim names the check
that proves it. What has not been checked is marked UNKNOWN.

Dragon's Dogma Online's revisions of these formats (ARCC, GMD 1.3.2, XFS 0x000F, tex 0x9D, mrl 0x22,
mod 210), measured on its whole client, are in `docs/ddo-bridge.md` section 3, with what ports between
the games in section 3a.

## Resource types

- 144 types occur across the 8,536 archives (358,431 resources).
- Every type id is `~crc32(class name) & 0x7FFFFFFF` (JAMCRC). For all 144,
  the class name is a string inside `DDDA.exe`. See `tools/survey_corpus.py`
  and the generated `src/riftstone/typemap.py`.
- Extensions follow ARCtool and Albam where they list the type. Otherwise they
  come from the payload magic.

Corrections to the plan that started this project:

| Plan said | Measured |
|---|---|
| DDDA is 64-bit | `DDDA.exe` is PE32 (x86), LAA already set |
| `.aibc` AI bytecode | No such type. AI is XFS: `rAIFSM` (.fsm, 4,385 instances / 3,073 distinct), `rAIGoalPlanning`, `rAIPriorityThink`, plus binary `rAIEnemyActionParameter` (.eap), `rAISensorExt` |
| `.ccl` is climbing collision | `.ccl` is `rChainCol` (chain collision, 4 files in the whole game). Grab/climb data location: UNKNOWN (candidates: `rObjCollision` .ocl, `rAreaHitShape`; `rRegionStatus` .rst is a creature's health, not that: `docs/enemy-hp.md`) |
| `em_param` enemy tables | No such type. Enemy data is spread over `rShlParamList` (.shl, XFS), `rAdjustParam` (.ajp), `rPropParam` (.prp, magic PRPZ: attack, defence, resistances, scale, EXP), `rRegionStatus` (.rst: the creature's health, `mHPMax`), `rStatusParam` (.statusparam, XFS) and others |
| Blender tools stuck on 2.79 | Albam 0.5.0 (MIT, Blender 4.2 to 5.x) imports and exports DD `.mod` v212, `.mrl`, `.tex`, `.sbc` today |

## ARC v7 (`src/riftstone/arc.py`)

```
0x00  "ARC\0"  u16 version (7)  u16 count
0x08  count x { char name[64]; u32 type; u32 stored; u32 size | flags<<29; u32 offset }
      zero padding to the next 0x8000
      payloads, contiguous, in directory order
```

Measured on all 8,536 archives (`tools/arc_layout` survey, then `check_corpus --only arc`):

- The data start is always aligned to 0x8000 and the padding is all zeros.
- Payloads have no gaps and follow directory order.
- `flags` is 2 on every one of the 358,431 entries. No name has bytes after
  its NUL.
- Every payload is a zlib stream that `zlib.compress(data, 6)` reproduces
  exactly. So parse → decompress → recompress → rebuild gives the original
  file bytes (8,536 of 8,536).
- Decoded sizes need 29 bits. Four `sa/DX9` shader archives exceed 16 MiB,
  which is where ArisenTools' 24-bit read silently truncated.
- Identity is the name bytes plus the type. Vanilla names include trailing
  spaces (`d_e0201_body_MM `) and doubled separators (`vo_ev_jp\\st100…`).
  `fsmap.py` encodes them reversibly as `%20` and `%_`.
- Order matters to the engine (per ARCtool's notes). Riftstone keeps the
  original order and appends new resources at the end. Whether additions need
  a specific position: UNKNOWN.

## XFS (`src/riftstone/xfs.py`)

MT Framework's reflected object serialisation. 44 resource types use it.

```
header  "XFS\0"  u16 0x0109  u16 class-version  u32 objects  u32 classes  u32 def_size
defs    at 0x14:  u32 class_offset[classes]            (relative to 0x14)
        class:    u32 hash  u32 engine_value  u32 nprops
                  prop x nprops: u32 name_offset  u8 type  u8 attr  u16 size  u8[16] zero
        names:    NUL-terminated UTF-8, first-use order, deduplicated, padded to 4
objects pre-order, numbered 0..n-1:
        u16 class<<1|1  u16 number  u32 size (from this field to the end)
        per property: u32 count, then count values
```

Values:

| Kind | Encoding |
|---|---|
| bool, u8..s64, f32, f64 | little-endian, the property's size |
| vector3/vector4/quaternion/float4, float2, float3 | 4/2/3 x f32 (vector3 carries a 4th lane) |
| matrix, hermitecurve | 16 x f32 |
| sphere, aabb, cylinder, rangef, rangeu16 | 4, 8, 12, 2 x f32; 2 x u16 |
| string (0x0E, size 36), cstring (0x20) | NUL-terminated bytes, no padding (UTF-8 here; Shift-JIS/cp932 in DDO's 0x000F files) |
| class / classref (0x01/0x02) | nested object, or `FE FF 00 00` for none |
| resource (0x80) | `02`, class name, NUL, path, NUL |

Measured on all 4,601 distinct vanilla XFS resources (`check_corpus --only xfs,yaml`):

- Parse → build is byte-exact, and the definition block can be regenerated
  byte-exact.
- Classes are always declared in first-use order, and none is ever unused.
- 540 classes appear, each with exactly one layout, and every class hash
  resolves to one `DDDA.exe` string. These make up the shipped
  `data/xfs_schema.json`.
- Properties without the array attribute (0x20) always hold exactly 1 value.
- No NaNs, no bool other than 0/1, and every string is valid UTF-8 (40,350 of
  486,494 values are not ASCII). YAML shows text in the file's encoding
  (`xfs.text_encoding`) and writes edits back in it; DDO's files are Shift-JIS
  (`docs/ddo-bridge.md` section 3).
- 13 classes repeat a property name. YAML writes the repeats as `name#2`,
  `name#3`.
- `engine_value` per class is kept as found. Likely the in-memory object
  size: UNKNOWN.
- The attribute bits (0x01, 0x04, 0x20 array, 0x80…) are kept as found.
  Only 0x20 is interpreted.

## YAML (`src/riftstone/yamlish.py`, `params.py`)

A strict subset, parsed to syntax only. Types come from the schema, so no
YAML implicit typing ever applies. XFS → YAML → XFS is exact for every
*canonical* file (classes in first-use order, compact name table), and every
vanilla file is canonical. A non-canonical file is refused by `unpack` and
`param` rather than silently changed. Floats print with the fewest digits
that round-trip through float32. A NaN keeps its exact bits as
`nan:0x7fc00000`.

## OCL — object collision (`src/riftstone/ocl.py`)

`rObjCollision` (.ocl), 8,629 instances, 378 distinct: a model's collision shapes, the attacks its hits carry
and the index that ties a motion's collision to both. Read with the game's own loader, `rObjCollision::load`
(`0x00CCB980`), which reads the file as a stream of 32-bit words (a float is its bits; a flag is a word tested
for non-zero). `rObjCollision::save` (`0x00CCC660`) writes the same stream. Names are the PS3 build's.

```
u32 mVersion 0x20121225, u32 mResourceID, u32 group count
  Group: mNo, mKind, mIsAttack, mIsDamage, mIsScrAdj, mIsObjAdj, mIsHang, mIsHanged, mIsCheck, mIsNotice,
         mIsNotice2, u32 node count
    Node: mNo, mShape, mRadius (f32), mJoint0, mOffset0 (3 f32), mJoint1, mOffset1 (3 f32), mRegionId,
          mHitPrio, mNodeAttr, mNodeEffectAttr, mNodeFreeWork
u32 attack count
  Attack: mNo (0x7FFFFFFF: an empty slot, nothing else follows), 83 values (`ocl.ATTACK`, in the loader's
          order, not the class's), two flag words (57 flags, `ocl.FLAGS`)
u32 sequence count
  SeqIndex: mNo, mModelID, mGroupNo, mAttackNo, mRelation
```

- The loader gives up on any other version: `3D 25 12 12 20` at `0x00CCB9BB`. An empty attack slot is
  `3D FF FF FF 7F` at `0x00CCBD3F`.
- The attack's flags: word 1 bits 0-31 and word 2 bits 0-24 fill the class's 57 flag bytes in order
  (`mMultiHit` ... `mIsForceShrink`, `mIsForceBlow` ... `mIsVSEnemy13`, `mIsAllGuard` ...
  `mIsSameFrameNoHit03`, and `mIsNoCalcEnemyDefence`, which only Dark Arisen's exe names); `mIsUseData`
  is not in the file, and word 2's bits 25-31 are read by nothing (the YAML keeps them as `flagsUnread`).
- The stream reader returns 0 past the end, and bytes after the last table are never read; Riftstone refuses
  both.

**Shapes, as the game builds them.** A hit record copies the node's shape (`8B 69 0C` at `0x0076FC0D`,
`89 A8 98 01 00 00` at `0x0076FC10`) and the entry node's update switches on it (`8B 8E 98 01 00 00` at
`0x0077388A`). Point 0 is `mOffset0` on joint `mJoint0`, point 1 `mOffset1` on `mJoint1`:

| mShape | Shape | Files |
|---|---|---|
| 0 | capsule from point 0 to point 1; its round ends reach past them by the radius | 3,587 |
| 1 | sphere at point 0 | 3,473 |
| 4 | capsule pulled in by the radius at both ends, so it stops at the two points (`C7 86 8C 01 00 00 04 00 00 00` at `0x007738BD`); points closer than twice the radius (`F3 0F 59 25 04 9E 4F 01` at `0x0077397B`) give a sphere one radius from point 0 towards point 1 | 249 |
| 2, 3 | nothing on that path (`83 E9 03` at `0x007738A0`); one monster family's own code (uEm5500) reads 2 as a box between the points and 3 as a box on `mJoint0` | 0 |

The radius follows the model's scale. A joint is the model's joint id; the node-position routine
(`0x00CCDE30`, with `0x00CCE190`) places a point on -1 the model's own frame (3,243 + 4,403 uses),
-2 the world's axes at the world origin (257), -3..-6 halfway between joints 14 and 18, 15 and 19,
16 and 20, 17 and 21 (-5: 33); a joint the model does not have leaves the point at the world origin.
The earlier model called 0 a sphere and 1 a capsule; the adjust path agrees with the table above
(`83 FB 01` at `0x0076FAD1`: shape 1 copies point 0 into point 1).

Measured (`check_corpus --only ocl`): the grammar reads **all 378 files to their last byte**, and parse →
build and the YAML (`ocl/2`) reproduce every one byte for byte: 6,166 groups, 7,309 nodes, 5,330 attacks and
2,159 empty slots, 6,592 sequence entries. The earlier YAML (`ocl/1`: a 20-byte "header", the body as hex and
"112-byte primitives" found by a scan — a group holding one node, seen 8 bytes in) still loads, and must
still make a file the loader reads. In game: UNKNOWN.

## GMD — text (`src/riftstone/gmd.py`, `text.py`)

`rGUIMessage` (.gmd), 16,338 instances, **7,954 distinct**, all version
1.2.1. Dialogue, item names and descriptions, menus, quest logs, pawn chatter.
Measured over all 7,954 (`check_corpus --only gmd`):

```
0x00  "GMD\0"
0x04  u32 version 0x00010201
0x08  u32 language        0 jpn, 1 eng, 2 fre, 3 spa, 4 ger, 5 ita, 7 zht (6 unused)
0x0C  8 bytes             zero in every file
0x14  u32 label count
0x18  u32 message count
0x1C  u32 label block size
0x20  u32 message block size
0x24  u32 name length, then the name + NUL ("TextWeb" in 7,852 files)
      label count x (u32 message index, u32 pointer)
      label block: NUL-terminated labels
      message block: NUL-terminated UTF-8 messages
```

- The label pointer is the authoring tool's address of the label text: one
  base per file plus the label's offset in the label block. Consistent in all
  1,824 files that have labels; Riftstone keeps the base (`label_base`) so an
  unchanged file rebuilds byte for byte, and new labels continue from it.
- Labels name messages in strictly increasing order (all 1,824 files), so each
  message has at most one label. 42 files repeat a label string.
- All 305,099 messages are valid UTF-8. Line breaks are mixed: 108,085 CRLF,
  16,366 bare LF, 6 bare CR, so the YAML keeps them as `\r\n` / `\n` escapes.
  Inline tags (`<ICON …>`, `<ITNO …>`, `<SIZE …>`, `{Herr}{Herrin}` gender
  forms) are plain text.
- **Result: 7,954 / 7,954 byte-exact** both ways (binary and YAML), 305,099
  messages, 93,846 labels.
- Language versions share a stem (`…_eng`, `…_fre`, …). `riftstone text add`
  appends a line to every version so the ids stay aligned.

What refers to a message (measured with every XFS resource in the game):

| Reference | Where | Target text file | Evidence |
|---|---|---|---|
| `cScenarioArg_Message.mMesId` | `scr/st###/etc/st###_mes` scenarios | `id/npc_wind/stage/st###_<lang>.gmd` | 278 of 278 references (with that file present) land on in-context text |
| `cFSMOrderParamMessage` (`mType`, `mQuestNo`, `mMesId`) | quest and stage FSMs | **UNKNOWN** | no single family fits: quest log, pawn and stage files each hold some ids and miss others |
| `cThinkFSMParamSetMessage` (`Type`, `QuestNo`, `MessageId`) | NPC/pawn think FSMs | **UNKNOWN** | same |
| `rAIPriorityThink::cCodeParam` (`MessTBL`, `MessID`) | pawn/AI think tables | UNKNOWN | not measured |

## FSM — AI state machines (`src/riftstone/fsm.py`)

`rAIFSM` (.fsm), 3,073 distinct, all XFS (edited as YAML, byte-exact).
`riftstone fsm` and `open` print a readable view: states, actions (the
process container and its non-zero settings), transitions with their
conditions as expressions, nested sub-machines, and the ids a YAML edit needs.
All 3,073 decompile with every link resolving to a condition.

Condition operators (`rAIConditionTree::OperationNode.mOperator`). The exe
has no names for them, so they are measured:

| Code | Meaning | Evidence |
|---|---|---|
| 0 | always (no operand) | 10,913 empty trees |
| 1 / 2 | is set / not | one operand, a flag (2 also wraps sub-conditions) |
| 3 / 4 | == / != | complement pair: same variable and constant, 59 places |
| 5 / 8 | < / >= | complement pair, 1,409 places; `Check Player Distance < 150` |
| 6 / 7 | <= / > | complement pair, 341 places; `RandomTimer <= 0` (expiry) 504 times |
| 10 | has bits | `SysCondition`, `FreeCondition` masks 1, 2, 4, 1024 … |
| 16 / 17 | and / or | every NPC schedule joined by 16 is an in-range window (`hour >= 7` and `hour < 19`, 170×), every one joined by 17 wraps midnight (`hour < 7` or `hour >= 19`, 112×) |

3–8 in that order is the usual `==, !=, <, <=, >, >=` enum; the schedule
windows confirm the direction (swapping it would make every AND window
impossible). Codes 9 and 11–15 never occur and print as `op<N>(...)`.

The executables confirm the table (2026-09-26): DDDA.exe's operator switch (`0x0117BD30`) calls
equality (`0x0117AD10`) for 3, `a < b` (`0x0117B200`, a signed compare) for 5 and `a > b`
(`0x0117B590`) for 7, and their negations for 4, 8 and 6; 9, 10, 16 and 17 have their own routines;
0, 11–15 and anything above 17 fall to a default that returns false. `native/fsm_exec` runs that code
on the game's own condition nodes (`OperationWorkNode`, `ConstWorkNode` over `ConstS32Node` /
`ConstF32Node`) for every operator with no to three constant operands, and nested operations:

| What | Result in the game's code |
|---|---|
| an operation with no operands | true, whatever the operator (`OperationWorkNode` `0x0117C040`) |
| 0, 11–15, above 17 with operands | false (so `op0(Check Pos Length)` never holds) |
| three or more operands | the operator on each adjacent pair; all must hold (`5(0, 1, 7)` is `0 < 1 < 7`) |
| one operand | 1 its truth, 2 the negation; 3, 5, 7, 16, 17 false; 4, 6, 8 true |
| 16 / 17 over sub-conditions (or one and an integer) | logical and / or |
| 16 / 17 over two integers | `a & b != 0` / `a \| b != 0` |
| 9 / 10 over integers | `a & b == b` (all the bits) / `a & b != 0` (any bit) |
| the first operand's type | decides: after an integer a float is truncated toward zero (`7 == 7.25` holds); a float first, or after a sub-condition for 9, 10, 16, 17, makes the result false |
| a float's own truth | false (`0x01179D50`, result type 3); an integer is true when not 0 |
| a condition with no root | false (`0x01179B7D`) |

## How the game runs a machine (`src/riftstone/fsmcheck.py`)

Read in both executables (DDDA.exe / DDO.exe, same code, other offsets) and, for DDDA, run in its own
code by `native/fsm_exec` on 6,000 random machines, every one agreeing with `fsmcheck.step`:

| Step | DDDA.exe | DDO.exe |
|---|---|---|
| each frame every running level (the root machine, then the sub-machine of its current state, ...) checks its current state's transitions, before its actions run or, with `mSetting` bit 1, after; not while the level's timer runs | `cAIFSM::move` `0x00E098F0` → `0x00E092A0` | `0x015A7870` → `0x015A7880` |
| the check: with `cAIFSM::Core.mAttribute` bit 2, the states marked "entered from any state" first, in list order, never the current state, one with `mSetting` bit 8 only once (its `mUniqueId` goes on a list); the first whose condition holds is entered and the links are not looked at | `0x00E06710` | `0x015A6270` |
| otherwise, with bit 1, the current state's links in list order: `mExistCondition` false = skipped; a condition id no tree has = skipped; the first that holds is taken | `0x00E05800` | `0x015A61F0` |
| its destination is the first state with that `mId`; none: nothing happens, and the links after it are not reached while it holds | `0x010C9290` | `0x015AAA60` |
| a condition is the first tree with its id | `0x0117AAA0` | `0x01844FE0` |
| `mAttribute` is 3 when a core is made | `0x00E05797` | `0x015A54AE` |
| entering a state with a sub-machine starts it at the first state whose `mId` is its `mInitialStateId` (none: that level does nothing); leaving the state ends it | `0x00E06A50` | not traced |

Runtime layouts (DDDA): cluster `+0x10` count, `+0x14` states of `0xAC` bytes; state `+0x04` `mId`,
`+0x08` `mUniqueId`, `+0x10`/`+0x14` links of `0x10` bytes (`+4` destination, `+8` exists, `+0xC`
condition), `+0x24` `mSetting`, `+0x2C`/`+0x30` the entry from any state; `ClusterDriveInfo` `0x28`
bytes (`+4` cluster, `+8` once list, `+0xC` current, `+0x10` next, `+0x14` go, `+0x15` came by an entry,
`+0x18` timer); `cAIFSM::Core` `+0x30` its condition tree (`+0x54` count, `+0x58` infos of `0x34` bytes:
`+0x2C` id, `+0x30` root), `+0xA8` `mAttribute`. DDO moves the timer to `+0x1C`, `mAttribute` to
`+0x88` and the entry fields to `+0x34`/`+0x38`. `rAIFSM.mFSMAttribute` (`+0x8C`) bit 2 only changes
how the setup builds each cluster's work (`0x00E097A9`); what that does is UNKNOWN.

What `riftstone fsm --check` (and the end of every readable view) finds, `check_corpus --only fsm`:

| | Dark Arisen (3,073 files, 5,770 machines, 40,942 states, 42,828 links) | Online (2,735 files, 3,677 machines, 36,196 states, 32,957 links) |
|---|---|---|
| problem: a link the game can take to a state that is not there | 0 | 18 in 14 files (event machines, destination `0xFFFFFFFF`) |
| problem / note: no start state | 0 | 1 empty machine (`quest\10310100\npc\spotwork_2_1`) |
| never taken: no condition | 27 in 19 files (all to `0xFFFFFFFF`) | 0 |
| never taken: the condition can never hold | 7 in 6 files: `GameHour > 22 and GameHour <= 6` (`go340_night`), `GameHour >= 22 and GameHour < 8` (`st240 go110_night`), `SceNo < 3400 and SceNo > 6500` (`bgm_call`), `SceNo < 1400 and SceNo >= 3600` (`chu_mes_st100`), `op0(...)` with an operand (4) | 1 (`op0(IsEndSetMotion)`) |
| never taken: a link before it always holds | 30 in 22 files | 3 in 1 file |
| note: never entered by the machine's own links | 767 in 254 files | 5,916 in 1,130 files |
| note: never left by the machine itself | 4,108 in 2,454 files | 2,592 in 2,469 files |

"Never entered" and "never left" are about the machine's own links and entries: quest scripts, FSM
orders and other code can move a machine (UNKNOWN), so they are notes, not faults. The four
impossible windows look like midnight or range tests written with *and* instead of *or*; what the game
does instead is UNKNOWN until played. A mod's state machine that adds a problem its game copy does not
have (a link to no state, a machine with states and no start) does not build (`mod.check_fsm`).
`riftstone fsm X --model M.json [--level N]` writes one machine as a NYR-Lang formal model
(`<path> formal fsm M.json`); `tools/fsm_crosscheck.py` has NYR-Lang's checker
explore every machine and compares its proofs with `fsmcheck`.

## ITL — the item list (`src/riftstone/itl.py`, `items.py`, `itemstats.py`)

`rItemList` (.itl): one file, `etc/item/itemList` in `rom/bbs_rpg`.
Measured (`check_corpus --only itl`: byte-exact both ways):

```
0x00  "ITL2"
0x04  u32 0x01330611      (a stamp; kept)
0x08  u32 count           1,901
0x0C  u32 0
then  count x 128-byte records; record N is item id N (all 1,901)
```

A record is `rItemList::PARAMETER`, 128 bytes. Every field has its engine name from the PS3 build. On the PC each is checked in DDDA.exe's code (**code**: the enhancement lookup and the
stat getters that call it, below), on the game's data (**data**), or keeps the PS3 build's layout, which the checked
fields around it agree with (**PS3**). The YAML (`itl/2`; `itl/1` still reads) shows every item's fields that differ
from 0 (mAlterItemNo: -1) by engine name; `raw` keeps the bits no field names; `riftstone items set <item>
mAttack=120 --mod M` edits one.

| Word | Bits | Fields | Checked |
|---|---|---|---|
| +0x00 | 0–10, 11–21, 22–31 | mAttack, mMagicAttack (11 bits each on the PC, 10 on the PS3), mElementAttack | code |
| +0x04 | 0–9, 10–19, 20–26 | mDefense, mMagicDefense, mCritialRate (PS3) | code |
| +0x08 | 0–9, 10–19, 20–26 | mShrink (stagger), mBlow (knockdown), mShieldStaminaReduceRate | code |
| +0x0C | 0–9, 10–19, 20–29 | mNokeGuard, mBlowGuard, mPoison (build-up) | code |
| +0x10 | 0–9, 10–19, 20–29 | mSlow, mOil, mBlind (build-up) | code |
| +0x14..+0x23 | s8 each | mSwordAttackRate, mHitAttackRate (PS3), mSwordDefenseRate, mHitDefenseRate, mFire/Ice/Thunder/Saint/DarkDefenseRate, mFire/Ice/Thunder/Saint/DarkCut, mBlowDefenseRate, mShrinkDefenseRate | code |
| +0x25..+0x33 | s8 each | mPoisonCut, mSlowCut, mBlindCut, mSleepCut, mWetCut (PS3), mOilCut (PS3), mEnemyCut, mSilenceCut, mSealCut, mCurseCut, mStoneCut, mAttackDownCut, mDefenseDownCut, mMagicAttackDownCut, mMagicDefenseDownCut (on consumables the same bytes carry the effect tags, "Secondary descriptions" below) | code |
| +0x34 | 0–15, 16–27, 28–31 | mLevelUpType (the enhancement row), mEnableEquipJob, mEquipKind (PS3) | code; mEnableEquipJob data: bits 1–9 the vocations Fighter, Strider, Mage, Mystic Knight, Assassin, Magick Archer, Warrior, Ranger, Sorcerer, as every weapon kind shows (swords: Fighter, Mystic Knight, Assassin; daggers: Strider, Assassin, Magick Archer, Ranger; archistaffs: Sorcerer), bits 10–11 on every item |
| +0x38 | 0–4, 5–9, 10–11, 12–14, 15–18, 19–21, 22–31 | mKind (which enhancement table), mCategoryType, mCategoryKind, mUseType, mUseMot, mElementType (PS3), mSilence (build-up) | code |
| +0x3C | 0–12, 13–25 | the item id; mAlterItemNo, the item it decays into (Scrag of Beast → Sour → Rotten; -1 none) | data |
| +0x40..+0x54 | | mEquipModelNo, weight (f32), buy, sell, mPri, mHp (f32) | data (weight, prices), PS3 |
| +0x58 | 0–28 | flags: mEnemy01..13BigDamage, mArrow, mMix, mVisor, mEnableThrow, mFake, mNoEffect, mRimShop, mEquipItem, mLantern, mInfinitUse, mHoldActionItem, mEquipActionItem, mGold, mRimPoint, mUnused, mKusariItem | data: mArrow on the 12 arrows, mLantern on the 6 lanterns, mGold on the 4 coin pouches, mRimPoint on the 10 rift crystals, mUnused on the 209 placeholders, mKusariItem on the 15 rotten foods |
| +0x5C..+0x7E | | mSeType, mLife, mFriendPoint, mFrindCategory, mObtainingLv, mStsAttackUp..mStsMagicDefenseUp, mStsFortune, mStsEconomicFortune, mTolerantDefense, mAddHp, mAddAp, mUpHp, mUpAp (f32), mOmId, mOmColor | PS3 |

Unnamed and kept in `raw`: +0x04/+0x08 bits 27–31, +0x0C/+0x10 bits 30–31, +0x24, +0x3C bits 26–31, +0x58 bits
29–31 (set on 73 items: flags added after the PS3 build), +0x6F, +0x7F; the three whole bytes are 0 on all 1,901.

- Name and description of item N are message N of
  `id/message/item/itemName_<lang>.gmd` and `itemInfo_<lang>.gmd` (1,903
  lines, no labels).

### Secondary item descriptions ("Inflicts Poison", "Cures poison", "Restores some Health")

Dante's "secondary descriptions" are the terse effect tags in the item detail
panel, not the flavour text in `itemInfo`. They come in two parts, both reachable:

- **The tag text** is `id/message/item/item_ability_<lang>.gmd` — 117 labelled
  lines (`item_abl_add_poison` = "Inflicts poison.", `item_abl_cure_poison` =
  "Cures poison.", `item_abl_cure_s` = "Restores some Health.", the category tags,
  etc.). It is a GMD like any other, so `riftstone` edits it today, in all seven
  languages.
- **Which tags an item shows** is derived from the item's use-effect data in its
  `.itl` record. The clearest piece is a **15-byte per-status array at +0x25..0x33**
  for consumables: one byte per status ailment, in this order —
  `poison, torpor, blindness, sleep, tarred, drenched, possession, silence,
  skill-seal, curse, petrification,` then four stat-downs
  `(Strength, Magick, Defense, Magick Defense)`. Measured from single-status items
  (Mithridate cures poison → +0x25; Bottled Haste cures torpor → +0x26; Throat
  Remedy cures silence → +0x2C; Secret Softener cures petrification → +0x2F) and
  from Panacea, which "cures all debilitations" and sets all 15 bytes to 1. 474 of
  1,901 items use the array. The value distinguishes kinds — a herb cure is 1, an
  accessory's resistance is 0x3C, a self-inflicting food (Avernal Mushroom) is 2 —
  but the exact scale is UNKNOWN. Throwables and arrows (Poison Flask, Poison Arrow,
  Sleeper Arrow) leave the array empty; their status comes from the weapon/projectile,
  not the consumable record.
- The array bytes are already editable through the `.itl` YAML's `raw` field with the
  offsets above; naming them (and the heal/stamina magnitude fields near +0x50) is a
  later layer. Heal amount, and how the game turns effect data into the exact tag
  list, are not fully decoded.
- **Free slots:** 209 ids are named "Unknown Item"; 175 of them also have
  price 0 (the rest still carry prices, so Riftstone leaves them alone). A new
  item takes one of these, so the list never grows past the 1,901 entries whose
  icons the game is known to handle.
- NaN weights keep their exact bits in the YAML (`nan:0x…`): converting
  through a float can change a NaN's payload (a fuzz finding).

Shops (`rShopList`, `.shp`, XFS) list stock as `mShopLineup` entries
(`cLineupData`: `mItemNo`, `mItemNum`, `mItemRearrival`, two condition
lists); unused condition slots are all zero. `riftstone items shop` appends
an entry with every condition cleared.

A new item made from a template keeps the template's record, so it enhances like the template (mLevelUpType) and
wears its model: every armour piece and accessory in the game (505) has an mEquipModelNo that is an `mArmorId` of
`model\pl\parts\m_parts.atr` (a weapon's has a model family 1..12 in its top byte, the Iron Sword 0x01000000; 135 of
the 276 weapons are also in that table). A new *model* needs its own entries there and in the `.amr` tables. UNKNOWN in game: such a new
model entry, and that a lineup entry with no conditions is always on sale.

### Enhancement levels (`LvParamWepon/Armor/Accessory.itemlv`, `itemstats.py`)

The level-stat tables (rItemLevelParam, binary `ite\0`: 0x1400, count, 432-byte rows; 260 weapon, 423 armour, 43
accessory rows) hold per-level lists, not item ids. sItemManager loads the three into +0x12C, +0x130, +0x134
(`89 86 2C 01 00 00` at `0x0045D8FF`, from `"etc\\item\\LvParamWepon"` at `0x0155E4C0`). How an item finds its row,
read in the lookup at `0x0045B620`, which every stat getter calls with the item, the stat's kind number and the
item's level:

- the table is chosen by the item's mKind (+0x38 bits 0–4) through a jump table (`83 C1 F9 83 F9 14` at
  `0x0045B65F`, kinds 7..27): 7–18 weapons, 19–24 and 27 armour, 25 accessories, 26 and the rest none. By the
  items of each kind: 7 swords, 8 maces, 9 shields, 10 magick shields, 11 longswords, 12 warhammers, 13 daggers,
  14 shortbows, 15 longbows, 16 magick bows, 17 staves, 18 archistaffs, 19 chest and 20 leg clothing, 21 head,
  22 chest, 23 arm and 24 leg armour, 25 cloaks, 27 outfit sets; 26 rings and the like;
- the row is mLevelUpType, the u16 at +0x34 (`0F B7 40 34` at `0x0045B65B`), refused past the table's count; rows
  are 364 bytes in memory (`69 C0 6C 01 00 00` at `0x0045B6A6`);
- levels 1 up to the item's level are walked with 2, 4, 6, 8, 8, 8 entries (`mUpParamLv<n>` the stat kind,
  `mUpRate<n>`, `mIsDirectValue<n>`); an entry for the stat sets the value to base × rate, or base + rate when
  direct, and the last one wins (the rate comes back in xmm0 and the base stays in xmm1). So each level's entry holds
  the whole change from the base: the Iron Sword's Strength 50 gets +16, +32, +48 at levels 1–3, +160 at 4,
  +510 and +650 at 5 and 6.

On the game's data every one of the 276 weapons, 462 armour pieces and 43 accessories has its row inside its
table (rows used up to 259, 422, 42 of 260, 423, 43: `check_corpus --only itl` repeats this). The stat kinds
(`itemstats.KINDS`, from the getters: `sItemManager::cItemParam`'s "RealValue" properties such as 重量, のけぞり値,
斬耐性 and the functions beside them): 0 mAttack, 1 mMagicAttack, 2 mElementAttack, 3 mShrink, 4 mBlow,
5 mShieldStaminaReduceRate, 6 mNokeGuard, 7 mBlowGuard, 8 mPoison, 9 mSlow, 10 mOil, 11 mBlind, 12 mSilence,
13 mDefense, 14 mMagicDefense, 15–21 the slash, strike, fire, ice, thunder, holy and dark resistances, 22–26
mFireCut..mDarkCut, 27–35 mPoisonCut..mStoneCut (without mWetCut and mOilCut), 36 mAttackDownCut,
37 mMagicAttackDownCut, 38 mDefenseDownCut, 39 mMagicDefenseDownCut, 41 both knockdown and stagger resistance.
The tables use -1 (an empty entry), 0, 1, 3, 4 and 6–41 but 40. `riftstone items stats <item>` shows an item's
stats at every level; in game, what the levels are called past the three stars is not read here.

## IST / IMX — drop tables and recipes (`src/riftstone/tables.py`)

A 12-byte header (magic, u32, u32 count) and fixed records. Measured
(`check_corpus --only tables`: all 3 files, 4,351 rows, byte-exact both ways):

`.ist` (item sets / drops): `etc/item/itemSetTbl` (2,800 sets: rewards and
gathering spots) and `etc/item/ItemEmListSetTbl` (1,116 sets: enemy drops).
A set is 20 × u16:

| Field | Evidence |
|---|---|
| set id | itemSetTbl 0–2799 (1350 twice); enemy ids up to 12,565 |
| `0xFFFF` | every set |
| two small numbers (f04, f06) | itemSetTbl 1–8 and 1–5; enemy table 0 and 0–8; meaning UNKNOWN |
| 8 item ids | `0xFFFF` = no item; every other id names a real item (one exception, 889) |
| 8 weights, one per slot | percent. All 2,800 itemSetTbl sets add up to exactly 100; enemy sets 604 × 100, 327 × 0, the rest 5–20. An empty slot with a weight is the chance of **nothing** |

Example: enemy set 3 is Small Fang 16%, Wolf Pelt 30%, Sour Scrag of Beast
12%, nothing 40%, Rift Fragment 2%, the wolf's drops. A placement can name its
own set: `mEmItemTable` in the enemy's layout record (25 of 5,525 enemy placements
do; -1 means the enemy's own). Which set an enemy's own drops use is decided by
enemy type in the exe: UNKNOWN which (`riftstone items sets <item>` finds sets by
what they drop). Objects name `itemSetTbl` sets the same way (`mSetTableID`,
`docs/world-map.md`).

`.imx` (recipes): `etc/item/itemMix`, 435 recipes of 4 × u32: ingredient,
ingredient, result, count. `count` is how many the recipe makes (Crimplecap +
Pine Branch makes 30 Poison Arrows; a potion recipe makes 1).

## LOT — spawn and object layouts (`src/riftstone/lot.py`)

`rLayout` (.lot), 6,209 distinct (6,342 names), all `lot\0` version 16. **Decoded completely, with
DDDA.exe's own grammar** -- the full write-up, every class's fields and the engine's limits are in
`docs/world-map.md`. In short:

```
0x00  "lot\0"   0x04 u32 16   0x08 u32 record count            rLayout::load           0x00CC1790
per record: s32 id, u32 kind (< 75), then the kind's class      rLayout::SetInfo::load  0x00CC1F60
            loads itself: its own fields, then its parent's ... cSetInfoCoord's last  (74 classes, 0x017EE988)
```

The 74 classes (`cSetInfoCoord`, `cSetInfoPawn`, `cSetInfoEnemy` and its 55 per-enemy subclasses,
`cSetInfoNpc`, `cSetInfoInsModel`, six object classes, `cSetInfoSensorTarget`) were read from each
class's PC loader and `createProperty`, so every field has the engine's name. `cSetInfoCoord` ends every
placement: `mSetID` s32, `mName`, `mOrder` u32, `mPosition`, `mAngle` (radians), `mScale`,
`mDrawDistance` f32 (-1: default), `mIsOnSplitAreaIgnore` u8 -- the "45-byte transform block" an earlier
version found by scanning (its unexplained `u32 0 or 4` is `mOrder`, its `+40` value `mDrawDistance`).

Measured (`check_corpus --only lot`): **all 6,209 distinct layouts parse to records with every byte
named, and rebuild byte for byte, binary and YAML (`riftstone: lot/2`)**; 48,424 records in 59 classes;
copy-then-remove restores every file, so records can be added and removed in all of them. The record id
is an id, not a position (a 0-based run in 3,281 files, another start in 1,023, not consecutive in 615):
a copy takes the largest + 1, a removal renumbers nothing, and ids stay in 0..1023, the loader's
id table. The largest + 1 counts the ids the record's group uses in its other layouts and the game's own
ids of the layout (even ones a mod removed: a machine of the game may name them), since the game finds a
placement by group and id and every group in the game uses each id once across its layouts; for an enemy
group, whose kill record keeps one bit per id and wraps past 31 (`docs/enemy-waves.md`), the smallest free
id under 32 comes first when the largest + 1 is not under it (`modfiles.group_ids`, `lot.free_id`). Mods
holding the earlier `lot/1` YAML (the file in hex plus the placements found) still load.

The file name says whose placements these are: `st<S>_<X>m<Z>n_<t><N>` is group N of `st<S>_<t>.gpl`
in map cell (X, Z) -- the name the engine itself builds (0x01562268); see `docs/world-map.md`.

## ARCS -- archive references (`src/riftstone/arcref.py`)

`rArchive` (resource extension `.arc` inside an archive), 9,990 instances, 1,038 distinct. The
resource's **name is another archive's path** (`rom\shell\shellhellhound` inside
`rom/enemy/em0200.arc`; `rom\om\f07\om3521` inside stage object packs; equipment inside the character
creator's pack), and its body lists that archive's contents:

```
0x00  "ARCS"   0x04 u16 version 7   0x06 u16 count
0x08  count x { u32 JAMCRC(resource name, all 32 bits), u32 type id }   in the referenced archive's order
```

Measured (`check_corpus --only arcs`): all 1,038 rebuild byte for byte; 1,034 list their archive's
directory exactly. Two in vanilla do not (older lists of `om8505` in some stage packs lack 7 quest-text
files added later; `om11001`'s hashes a differently spelled effect name), so the game already lives
with out-of-date lists; the other two, in stage802 and stage804, reference archives the game does not
ship (`id\credit_02\credit2`, `id\DDN\DDNcredit_01\creditDDN`). More of either than those counts fails
the check (`ARCS_MEASURED`, 2026-09-26); what the engine uses the lists for is UNKNOWN. This is the
archive dependency graph: enemies pull in their projectile (shell) archives, stage packs their object
archives (`riftstone world deps <archive>` shows both directions).

## GPL -- enemy group placement (`src/riftstone/gpl.py`)

`rLayoutGroupParamList` (.gpl), 194 distinct. The format behind the "3 unit kinds per group" limit
(the community unit-expander patches the exe that reads ``mUnitKindList``) and the per-group spawn
cap ``mSetCountMax``. Layout from dd-tools' `gpl2xml.c`, proved byte-exact on all 194
(`check_corpus --only gpl`): 3,925 groups, 2,694 unit-kinds.

A file is: magic `gpl\0`, version, an id list, a set-bit list, a DLC number, then group records. A
group has a packed 32-bit field (group id 9 bits, priority 18, split flag 1, DLC 4), an
`mUnitKindList` (enemy name + belong flag), an `mLayoutIDArray`, ~44 scalar conditions (spawn
time/flags/respawn, and `mSetCountMax`), and three kinds of area shapes -- hit, life (an
array-of-arrays), kill. A shape is a tagged record: `type` 1 box (4 vertices + concave data), 2
point, 3 capsule (two positions + radius). f32 is kept as exact bits; array counts are read
unsigned so an absurd length is a clean refusal.

Measured since (`docs/world-map.md`): a stage loads `scr\st<S>\etc\st<S>_{e,n,p,t}.gpl` (enemies, NPCs
and hostile humans, objects, AI sensor targets); `mGroupList` is the 295-slot group table (entry N is
`0x80000000` when group N exists: 3,925 of 3,925), so group numbers are 0..294; every
`mLayoutIDArray` entry holds the stage id and the owning group, listing the map cells the group spans;
group N's placements are the layouts `st<S>_<X>m<Z>n_<t>N`. `mSetCountMax` is a group's total output:
with respawn type 5 its placements are spawn points it refills (stage 330's group 35: 100 goblins and
hobgoblins from 7 points). The on-screen limit is still the exe's (~10 enemies).

**Pre/post-Dragon spawns (measured):** a group's `mAppearBgn`/`mAppearEnd` are the **story-scenario
window** it spawns in. The Dragon-kill boundary is **scenario 7800**: pre-dragon groups end at
`7799`, post-dragon begin at `7800` (91 vs 33 groups; 7 field stages carry both — st100/200/240/300/701).
Editing these fields moves a spawn across the Dragon fight. Other thresholds (7000/7150/7600) are
separate quest beats.

**"After a group is cleared" (measured, corrected):** `mSetCondition.mIsEmGroupLink` = 1 with
`mSetCondition.mLinkEmGroup` = M makes a group wait for group M. Only 9 groups use it, **all in NPC and
object lists** (st100 `_n` 121, 122, 123, 126, 127; st443 `_p` 12; st444 `_p` 12, 13, 14), and M is a
group of the same stage's *enemy* list: NPCs or objects appear once those enemies are dealt with. No
enemy group uses it, so it is not a proven enemy-wave mechanism (an earlier note here said it was).
Enemy "rounds" in the game are hordes: a spawn cap with respawn type 5 (above), or a quest FSM that
waits for a group to die (`cFSMOrderParamCheckEnemy`, `mCheckFlag: 3`) and then turns on the lot flag
that gates the next group (`cFSMOrderParamSetLayout`; `quest/q0012_b00`,
`docs/fsm-grigori-and-waves.md`). `mRspnCondition.mRspnType` in enemy lists: 1 (767
groups), 0 (330, once), 3 (29), 5 (21, the capped hordes), 4 (19), 2 (5), 6 (4); what 2, 3, 4 and 6 do
exactly: UNKNOWN.

## TEX -- textures (`src/riftstone/tex.py`)

`rTexture` (.tex), 11,221 distinct (73,706 counting every copy) -- the single most common editable
asset, behind every armour, monster, face and UI reskin. Dragon's Dogma texture revision 0x99,
proved byte-exact on the whole corpus (`check_corpus --only tex`): all 11,221 parse→build
byte-for-byte (cube maps included), and all 11,199 flat textures round-trip `.tex → .dds → .tex`
byte-for-byte.

Header (little-endian): magic `TEX\0`; word1 = `version:12` (0x099) `| attr1:20` (0x20000 flat,
0x60000 cube, 0x30000 on one volume texture: the only three values among all 11,221; DDO's 0x9D
textures set bit 1 on every one and 0x1000/0x2000 on 403 high-memory variants, bits DDDA never
uses, so a texture changing game keeps only the shape nibble, `tex.attr1_for`);
word2 = `mipCount:6 | width:13 | height:13`; word3 = `depth:8` (1 flat, 6 cube)
`| format:8 | attr3:16`; then `mipCount * depth` absolute u32 offsets; then the pixel data, mips
largest-first, tightly packed. The pixel payload is kept opaque, so the rebuild is exact for every
texture regardless of format.

The pixel-format ids and their sizes were **measured against the game's own mip sizes**, not copied:
8-byte-block (BC1 family) 19/20/25, 16-byte-block (BC3/BC5; 31 is normal maps) 24/31/37/43/47,
4-byte uncompressed 40. `to_dds`/`dds_to_tex` write and read a standard `.dds` (DXT1/DXT5/ATI2 or
BGRA) so the texture opens in Photoshop, GIMP or Paint.NET; `dds_to_tex` restores the exact original
format id from the source `.tex` (the `--like` template), which is why a round trip is byte-for-byte
even though a legacy `.dds` cannot express the DDDA format id's sRGB nuance. Cube maps and one
oddity export as raw `.tex` only. `riftstone tex info | to-dds | from-dds`. UNKNOWN: the exact DXGI
identity within each block-size class (BC2 vs BC3, BC1 vs BC4) -- irrelevant to the byte-exact round
trip, and the block bytes carry through unchanged.

## MRL -- materials / shaders (`src/riftstone/mrl.py`)

`rMaterial` (.mrl), magic `MRL\0`, revision 0x20; 3,458 distinct, all parse->build byte-exact
(`check_corpus --only mrl`). This is Dante's "see what shaders are used where". Layout (reversed from
`rMaterial::load`, PS3 `0x010015D0`, named):

  header (0x1C): magic, version, matCount, texCount, typeHash, texTableOff (0x1C), matTableOff;
  texture table -- texCount x 0x4C: `u32 type_id` (rTexture 0x241F5DEB), 2 u32, `char name[0x40]`
    (NUL-terminated then original 0xCD padding, kept verbatim);
  material table -- matCount x 0x3C: `u32 shaderHash` (which shader the material uses), `u32
    materialHash`, then 13 u32 (param offsets, shader sub-hashes, texture/flag refs);
  then the shader-parameter data, kept opaque so the rebuild is exact.

`riftstone mrl info <file>` lists each material's shader hash and every texture it binds (27,673
bindings across the game); `riftstone mrl retex <file> --old <name> --new <name>` repoints a texture
in place (only the fixed 0x40 name field changes). Full per-material parameter editing (the
`cDDMaterialCtrl` getShader* block) is a later layer.

## PRP -- enemy / character parameters (`src/riftstone/prp.py`)

`rPropParam` (.prp), magic `PRPZ`; 223 distinct, 21 enemy classes, all byte-exact both ways
(`check_corpus --only prp`). These are the `charparam\em\em####` files -- the single most important
files for rebalancing. A `.prp` is a **12-byte `PRPZ` wrapper around a standard XFS document**, so
editing is the existing XFS parameter pipeline (`riftstone param <file>`, byte-exact) and no new
serializer was needed:

  header (0x0C): `PRPZ`, `u32 marker` (always `0x77CED14C`), `u32 classHash`;
  body: a normal `XFS\0` document (version 0x0109) starting at +0x0C.

The wrapper carries no data of its own -- across every vanilla file the marker is constant and the
class hash equals the XFS root class -- so `prp.build` reconstructs the header from the body and the
bytes are fully determined by the XFS. Verified by disassembling PS3 `rPropParam::load`
(`0x0100C184`, named): it reads the magic, checks `0x77CED14C`, resolves the class via
`MtDTI::from`, then deserialises the XFS.

The parameter names are the developers' Japanese strings, kept byte-for-byte. The common enemy class
`0x7E509FE9` (165 of 223 files) carries: 攻撃力/防御力/魔法攻撃力/魔法防御力 (attack, defence,
magick attack/defence), 体重/大きさ/スケール値 (weight, size, **scale value**), a full elemental and
status resistance table (耐炎/耐氷/耐雷/耐聖/耐毒 ...), のけぞり/ぶっとびガード (flinch/knockback
guards), 人間敵 HP (human-enemy HP), 経験値 (EXP) and fall thresholds. `prp.GLOSS` translates the
class for reading, and `riftstone open` / `inspect` print the recognised stats in English. Enemy
scale lives here (`scale value`) as a per-class field; `size` is a 0/1 class set on the big monsters
(and oxen), not a scale, and the game does not scale weight with size (`docs/re-size-scaling.md`) --
see `docs/re-enemy-cap.md` for how this relates to the placement-level scale in `.lot`. 人間敵 HP is the health
of the human enemies only; a monster's base health is `mHPMax` in its `.rst` (`docs/enemy-hp.md`).

## Flat parameter formats (`src/riftstone/flat.py`)

Twenty-two small binary types share one shape -- a 4-byte magic (four have none), then scalars and
length-prefixed record arrays. One schema-driven engine reads them all; each format is a schema
whose layout came from Chris Purnell's dd-tools (`vendor/dd-tools-main`), or from the PS3 loader
where dd-tools had none, and is proved byte-exact against every instance in the game
(`check_corpus --only flat`, 3,097 files). Types: `i8 u8 s16
u16 i32 u32 s64 f32 string`; f32 is stored as its exact 32-bit bits so NaN payloads survive. Lists
come three ways: an inline u32 count (`L`), a count named earlier in the same record (`LN`), and a
fixed length (`A`); records nest, so a three-level table like `.qct` is still just a schema.

Welded so far (class, count): rAdjustParam `.ajp` 55, rEditPawn `.edp` 518, rHumanEdit `.hed`
506, rFaceEdit `.fed` 504, rBodyEdit `.bed` 483, rHumanPartsEdit `.hpe` 321, rItemLevelParam
`.itemlv` 3, rItemRandParam `.irp` 1, rItemCurseCnv `.cit` 1, rSkillList `.skl` 1,
rNpcLedgerList `.nnl` 1, rArmorModel/PartsOff/Table `.amr`/`.aor`/`.atr` 3/1/2, rGUIFont `.gfd`
3, rEquipLvUp `.qlv` 1. `.itemlv` is the 432-byte record measured earlier: per-enhance-level
stat lists (2/4/6/8/8/8), confirming it is not keyed by item id.

Who an NPC is, and what they wear (read from the game's files, 2026-09-30; asked by a Nexus player: do the Gran
Soren knights draw their armour through the player's item table, `itemList.itl`?). A placed NPC is a `cSetInfoNpc`
record in a stage's `_n` layout (`riftstone spawns list scr/st220/etc/st220_00m00n_n140.lot`). Its fields name no
item: `mNpcId` (127 for Geffrey in stage 220), `mSimpleModelType`, `mPartsVariationNo`, `mClothType`,
`mHumanEnemyKind` / `mHumanEnemyID` (hostile humans only), `mGoodsOff`, its schedule and AI settings. `mNpcId` is
an entry of the NPC ledger `etc/item/NpcList.nnl` (727 entries in `rom/bbs_rpg`, `riftstone extract
etc/item/NpcList.nnl`), and that entry holds the stage, friendship, `mModelType` (0 or 1 in all 727),
`mIndex` (the model number: 70 for Geffrey), voice, shop type, a small `mLikeItem` (1, 2, 4, 8: a liking, not an item id)
and names -- no item id and no equipment (its 16 fields are `mNo`, `mStageNo`, `mInitmFriendPoint`, `mModelType`, `mIndex`,
`mStrayId`, `mShopType`, `mSeType`, `mFlag`, `mChild`, `mTP`, `mTPType`, `mLikeItem`, `mCivilian`, `mName`, `mNameJ`). The
named knights ("Ser Henning", "Ser Maximilian" ...) are ledger entries like any other (ids 137-143 in stage 220).
So a town NPC's look is a model chosen by (`mModelType`, `mIndex`) and the placement's parts variation, not a
list of items from `itemList.itl`, and the item table's unused slots (`Unknown Item`, price 0) hold no NPC-only
gear as a result. **Not measured:** how (`mModelType`, `mIndex`) becomes a model file, whether those models share
meshes with the player's armour, and what hostile humans (bandits, adventurers) are dressed from.

AI and quest tables added the same way: enemy-action params `.eap` 54 and stage-action params
`.sap` 187 (both carry the shared `ActionParam` record: which AI action fires under which status,
element, flag and study conditions), magic-act timing `.map` 4 (per-spell motion/effect frames and
three per-level shot-control blocks), and quest control `.qct` 325 (judgment/result command rows,
nested sheet -> table -> row). `FreeF32` is shown as a float in both eap and sap for readability;
the bytes are identical to reading it as i32.

`.rst` rRegionStatus (106 files, magic `0x20110930`) is the same nested-list shape and was added
from its PS3 loader (`rRegionStatus::load`, `0x00B07918`, named) rather than dd-tools: a
list of region sets (`mRegionStatusList`: `mNo`, `mType`, then `mElementList`), each holding a list of
56-byte elements (two u32, seven f32, three u32, two f32, in disk order). These are the creature's
health and body-part values, and `mHPMax` (the third value of an element) is the base health the game reads for a
monster (2026-10-02, `docs/enemy-hp.md`: the property names in DDDA.exe, `initRegionStatus`, `getHp`, the
resource lists that load each file). The fields carry the executable's own names (`mNo`, `mId`, `mHPMax`,
`mDPMax`, `mDPSpeed`, `mBPMax`, `mBPSpeed`, `mDamageAdj`, `mHitStopAdj`, `mSurface`, `mSeSurface`, `mAttr`,
`mDPResetTimerMax`, `mBPResetTimerMax`); YAML `rst/1` files, which named them by struct offset (`at0c`, ...) and
the sets `mpRegion`/`mpSub`, still load. The round trip is byte-exact.

Magic-less formats (bed/fed/hed/hpe) start with `version`, so they are recognised by resource
type, not content. See `docs/vendor.md` for the tool provenance.

**Dragon's Dogma Online's vocation tables** (2026-09-25, `docs/ddo-vocations.md`), all magic-less but
`jtq`, proved on every file of the client (`check_corpus --game ddo --only flat`): rJobCustomParam `.jcp`
11 (u32 version 1, then per custom skill a u32 skill number and 17 resource references, each
`(JAMCRC(path), type id)`: 140-byte records), rAcquirement::rCustomSkillData `.csd` 11 (u16 skill no,
u16 message index, u16, u16 base skill, u8, then a counted level table of `u16 level, u16 job level,
u32 job points`), rAcquirement::rNormalSkillData `.nsd` 11 (15-byte records), rJobMasterCtrl `.jmc` 1
(nine u32 named by DDO.exe's `cJobMasterCtrl` properties), rItemEquipJobInfoList `.eir` 1 (job bit masks),
rWepCateResTbl `.wcrt` 2, rDmJobAdjParam `.dja` 1 (job type + 15 f32), rJobLevelUpTbl2 `.jlt2` 9 (five u32
per level) and rJobTutorialQuestList `.jtq` 1 (`JTQ\0`, u16 version, the quest ids).

**Another game's revision of a format** (`flat.REVISIONS`): when a resource lacks the format's own
magic, the listed revisions are tried and the parsed table keeps the revision's key, so its YAML tag
(`riftstone: ajp-ddo/1`) and rebuild follow it. DDO's rAdjustParam has no `ajp\0` magic: u32 version
0x100, then the float list (`ajp-ddo`, 65/65 files: enemy params and the player's `baseStatus`).

## LMT -- motion lists (`src/riftstone/lmt.py`, `lmtcodec.py`)

`rMotionList` (.lmt): DDDA version 66 (1,002 distinct, 4,313 entries), DDO version 67 (1,402); all
byte-exact (`check_corpus --only lmt`), every keyframe buffer split into keys and packed back identically
(420,900 + 675,678 buffers, 55 M keys). The full write-up -- layout, flags, the twelve codecs and their bit
maps, the loaders (`rMotionList::load` DDDA 0x00E9D330, DDO 0x015A4B80), the one semantic difference
between the games (codecs 11-13), the port and the skeleton comparison -- is `docs/animation.md`.

In short: header `LMT\0`, u16 version, u16 count, u32 offsets (0 = empty slot); 60-byte motion headers on a
16-byte stride; 36-byte bone tracks (codec, usage, bone type, joint id, weight, buffer, reference value,
extremes); 4 event groups of 72 bytes; `(flags >> 16) & 0x1F` float groups of 12 bytes. Blocks shared
by reference stay shared (bit 24 marks a motion reusing an earlier motion's track array; both loaders
skip relocating it). Codec 11-13 tracks: DDO through extremes, DDDA signed with the reference supplying
the unstored axes.

## EAN -- effect UV animation (`src/riftstone/ean.py`)

`rEffectAnim` (.ean), magic `EAN\0`, version `0x20100924`; 39 files, all byte-exact
(`check_corpus --only ean`). Dragon's Dogma Online uses the same container with version `0x20120224`
(50 files, all byte-exact; its per-frame records are not always 56 bytes -- 24 files differ -- so the
payload stays opaque there as well). The loader (`rEffectAnim::load`, PS3 `0x01027618`, named) reads
a payload byte count and a frame count, then copies the whole payload into one buffer with a single
`memcpy` -- so the body is opaque to the engine, and Riftstone keeps it opaque too:

  0x00  `EAN\0`   ·   0x04  u32 version 0x20100924   ·   0x08  u32 payloadSize (= size - 0x10)
  0x0C  u32 frameCount   ·   0x10  payloadSize bytes (frame data)

The payload is two parallel per-frame arrays (`frameCount x 32` then `frameCount x 24`; every file is
`frameCount * 56`); their field layouts (UV rect, timing) are not decoded, so editing today means
swapping the whole animation. `inspect` reports the frame and byte counts.

## Weather, fog and sky (`src/riftstone/weather.py`)

One schema engine (`KINDS`) for every weather resource of both games; each grammar was read from the
game's own loader (DDDA.exe / DDO.exe, names from the PS3 build where it has them). Editable as
YAML (`riftstone param`; tags `<ext>/1`, the file version is inside). `check_corpus --only weather`,
2026-09-25: DDDA 71/71 (wep 57, wfp 12, sky 2), DDO 151/151 (wep 83, sky 1, wtf 25, wte 19, wtl 15, wsi 7,
wta 1), each parse -> build and YAML -> bytes (through `params`, as the editor does) byte-exact.

| Ext | Class | Game, version | Body |
|---|---|---|---|
| `wep` | rWeatherEffectParam `wep\0` | DDDA 1 | six counts, then six lists (CORRECT_TYPE_01/04/06/07/08/09) of 44-byte rows: mHour, mMinute, mColor (bytes r g b a), mColorBlend, mIntensity, mIntensityBlend, mEnvMapPowerScale, mShadowColor, mShadowColorBlend, mShadowIntensity, mShadowIntensityBlend |
| `wep` | same | DDO 3 | seven lists, each its count then 40-byte rows: `mTime` (ms since midnight, always a whole minute) replaces hour/minute |
| `wfp` | rWeatherFogParam `wfp\0` | DDDA 1 | rows mHour, mMinute, mDensity, mExponentDensity, mStart, mEnd, mColor (xyz floats) -- the loader's order |
| `sky` | rSky `SKY ` | both, 6 | the atmosphere / sun / moon model (mRamda 665/555/455 nm, Earth radius 6,367, mObliquity 23.4, sun and moon radius and distance) and the sun / moon texture paths (at most 258 bytes each, longer is refused) |
| `wtf` | rWeatherFogInfo (no magic) | DDO 3 | rows mTime, mStart, mEnd, mExponentDensity, mColor (names inferred: DDO.exe registers none) |
| `wte` | rWeatherParamEfcInfo (no magic) | DDO 1 | rows mWeatherId (1..3) -> an rWeatherEffectParam reference |
| `wtl` | rWeatherParamInfoTbl (no magic) | DDO 12 | per weather id: cWeatherParam (named from cDarkSkyParam's properties by offset), the fog info, cloud models; mUnkXX fields UNKNOWN |
| `wsi` | rWeatherStageInfo `WSI_` | DDO 7 | six resource references (skies, scheduler, star model / texture / catalog) and the star / env-map settings |
| `wta` | rWeatherInfoTbl (no magic) | DDO 17 | per weather id two sets of weather-script commands (cWSCSound, cWSCSoundRnd, cWSCSoundVolume, cWSCEpv, cWSCTimer); ids are `(class id << 32) \| JAMCRC(path)`, confirmed on all 54 sound commands |

The magic-less DDO tables are routed by resource type (`params.TYPED`). UNKNOWN: what each CORRECT_TYPE
(and each of DDO's seven lists) colours; the mUnkXX fields.

## LCM -- camera lists (`src/riftstone/camera.py`)

`rCameraList` (.lcm), magic `LCM\0`. `check_corpus --only lcm`, 2026-09-25: DDDA 99/99 (version 3, 999
cameras, 142,153 frames), DDO 52/52 (version 5, 1,101 cameras, 156,591 frames), parse -> build and YAML
round trip byte-exact.

- **DDDA v3:** one row per frame: eye position, look-at target, a unit quaternion (all 142,153 are unit
  length) and fov in degrees; `fovtype` is uCameraBase::FOV_TYPE (0 FOV_V, 1 FOV_H).
- **DDO v5:** four tracks per camera feeding uCamera's mCameraPos, mTargetPos, mCameraUp (the Y axis of the
  rotation) and mFov. The track struct has LMT's 36-byte layout, but the camera evaluator has its own codec
  table and decodes packed values as (q - 8) / (2^n - 16), not LMT's / 65535: every packed value lies in
  [8, 2^n - 8], every track's first key decodes to its reference within one step, and every keyed track's
  frame counts sum to frame_num - 1. Layout: blocks, zero pad to 16, all extremes, then each camera's
  buffers on 16-byte boundaries (`parse` accepts only this layout). Keys are edited raw (each shown with
  its decoded value); there is no float -> key encoder. UNKNOWN: the camera userdata (always 0),
  usage/unk2/unk3/weight in DDO tracks.

## Sound cues (`src/riftstone/sound.py`)

Ten binary forms of seven resource types, one module; every grammar is the game's own loader (the save
functions fixed the byte order), DDDA's names from the PS3 build, DDO's from DDO.exe code that does what
DDDA does with the named field. YAML via `params` (tags `srq/1`, `srq-ddo/1`, ...). `check_corpus
--only sound`, 2026-09-25, parse -> build and the params YAML round trip byte-exact on every file:

| Ext | Class | DDDA | DDO | What it holds |
|---|---|---|---|---|
| `srq` | rSoundRequest (`SREQ` 0x13 / `SRQR` 3) | 1,600 | 2,399 | sound-effect cues -> package (.spc) or bank (.sbkr): program, volume (dB), pan, pitch, sends, priority, limits, speaker sections |
| `stq` | rSoundStreamRequest (`STRQ` 0x1B / `STQR` 2) | 2,632 | 1,132 | streamed cues (music, voice) and their stream source table |
| `srd` | rSoundRandom (`SRND` as a u32, bytes `DNRS`, 2) | 256 | - | 16 (cue, weight) pairs per entry; a cue with mCommand 4 plays one |
| `smx` | rSoundSubMixer (`SMX\0` 3 / `SMXR` 1) | 49 | 35 | fader settings |
| `spl` | rSoundPhysicsList (`SPL\0`) | 29 | - | .spr paths and a 64-entry index |
| `sbkr` | rSoundBank (`SBKR` 4) | - | 1,734 | programs -> weighted waves (.xsew); every program's total equals its waves' weights |
| `sar` | rSoundAreaInfo (`sar\0` 0x11) | - | 684 | per-area music numbers, zones, a request per ground surface |

Requests and stream requests are memory images (offsets become pointers at load); `parse` refuses any
layout `build` would not reproduce. Volumes are decibels: gain 10^(dB/20), -96 or less silent (same code
in both games). DDDA audio now has `riftstone-audio` (`docs/audio.md`): SNGW Vorbis
extraction/build and loop editing, plus `.spc` (rSoundPackage, `SPAC` 0x0C,
1,490 files) split RIFF/MS-ADPCM extraction and format/size-preserving replacement.
Unknown SPC metadata-table bytes are retained. DDO `.xsew` (rSoundSourceMSADPCM,
plain RIFF/WAVE, 13,883 files) is not added to that DDDA CLI profile.
UNKNOWN: DDO element fields named mUnkXX (probably DDDA's delay / booking / kill-time / centre-volume /
doppler fields by value range, not proven), the bank's other wave bytes and 8-byte records, spl's mUnk0C /
mUnk10, speaker-set modes, what the sound zones do; how any edit sounds in game.

## Effects (`src/riftstone/effect.py`, `effect_efl.py`, `effect_e2d.py`, `effect_efs.py`)

Grammars from the loaders of the PS3 build, DDDA.exe and DDO.exe; names and enums from the PS3 debug
info. `check_corpus --only effect`, 2026-09-25 (YAML through `params`):

| Ext | Class | DDDA | DDO | YAML | Decoded |
|---|---|---|---|---|---|
| `epv` | rEffectProvider (`epv\0`, DDDA 0 / DDO 22) | 599 | 1,660 | yes | every field: indices -> elements (up to 8 `.efl` + one `.e2d`, joint `mJointNo`, position, camera offset, direction, scale, colour, loop, DDDA's 3 LOD levels), the motion-sync list (motion number + frame window -> element: DDDA 8,960, DDO 7,855 links) and events |
| `efl` | rEffectList (`EFL\0`, 0x20110318 / 0x20120306) | 4,514 | 5,754 | yes | header, 16-byte unit entries, joint table; generator and particle heads as named fields (65.0% / 60.6% of all bytes); other structures kept as byte regions, offsets rebuilt from region order |
| `e2d` | rEffect2D (`E2D\0`, 0x20110314 / 0x20120306) | 156 | 52 | yes | 6 render-target / back-texture paths, units (53.9% / 45.2% named) |
| `efs` | rEffectStrip (`EFS\0`, 0x20080912) | 25 | 6 | no | part table; 32-byte vertices and 8-byte records kept as bytes; header totals checked |

Bit fields pack from the lowest bit on PC (proved on the header: the 63 DDDA / 51 DDO files with the
unit-generator bits set are exactly the files with a unit generator). `effect_efl.resources()` lists every
file an effect loads (textures, effect anims, models, strips, sound requests, other effect lists), and
`effect.links()` the motion -> effect links; with the `.jcp` path hashes that gives skill -> motion ->
effect lists -> textures. UNKNOWN: DDO's added `.epv` fields (`mUnk00` gates loading above 0x3040), the
`.efl` joint / life / move bodies and particle bodies past the heads, particle type 25, DDO life types
7 and 8, the `.e2d` members, the `.efs` vertex fields; DDO's `.efl` layout is assumed equal to DDDA's
(offsets and paths land identically), not proven from DDO.exe.

## Faces, conversations, schedules, zones (`facial.py`, `msgset.py`, `schedule.py`)

Grammars from the loaders (DDDA.exe, DDO.exe) with names from the PS3 build and the engines' property
lists. By magic in `params` (both games' `.mss` share one type id with different magics).
`check_corpus --only fca,msgset,sdl,zon`, 2026-09-25, byte-exact with the params YAML round trip:

| Ext | Class | DDDA | DDO | Body |
|---|---|---|---|---|
| `fca` | rFacialAnimation (`FCA\0`) | 10,133 (1,678,543 keys) | - | 60-byte header; per track a key count, a default value and 36-byte `MtFCurve::DescKey` keys (frame, interpolation, value, rtany, ltany, right, left, rtanx, ltanx); interpolation 0 default, 1 step, 2 linear, 3 Bezier, 4 Hermite (`MtFCurve::getValue`); every file has 14 tracks, every key linear with zero tangents |
| `mss` | rMsgSet (`mss\0`; DDO `mgst` v3) | 830 (8,676 conversations) | 3,187 (27,089 groups, 43,310 lines) | DDDA: 250-byte `cParam` records, up to 15 lines and 6 choices with jumps; both DDDA loaders write a third 15-entry array over `mQuestNo_msg`, so quest-flag conditions are never checked (8 conversations set one; in game UNKNOWN). DDO: groups and lines with presence bytes |
| `msl` | rMsgSerial (`msl\0`) | 752 (12,478 serials) | - | a list of u16 `mNo` |
| `sdl` | rScheduler (`SDL\0`, DDDA v19 / DDO v22) | 624 (88,812 tracks) | 1,481 (325,912 tracks) | header, 24/32-byte track records (14 / 16 kinds, each its value size), keys of a 24-bit frame and 8-bit mode (0 hold, 2 trigger, 3 linear, 5 curve, 1 / 4 count up); the string table's writer quirk (it searches the table, suffixes included, but skips the entry it appended last) reproduced on all 2,105 files, so names are plain text |
| `zon` | rZone (`zon\0`) | 477 (12,647 layouts) | 1,698 (17,898 layouts) | memory header, an embedded pool of XFS objects (exact bytes; `zone_xfs()` decodes 7,965 / 8,022 DDDA and 8,799 / 9,019 DDO -- the rest use property type 0x0F, colour, which `xfs.py` lacks), layouts with all 12 ShapeInfo shapes, group managers, grids, bounding boxes; `LayoutIndexFromUniqueID[mUniqueID]` equals the layout's index everywhere |

UNKNOWN: `.fca` right / left and which face control a track drives (`.fcp`); what `.msl` serials index;
`.sdl` mUnk08, the flag bits at 0x0C, DDO kinds 7 and 10 (no file uses them; keys refused); `.zon`
mUnk64, ContentsNum, group mUnk08 / 0C, a grid cell's second word, DDO's mUnkTable, 7 type-2 zones
without a grid. In game: none of these was tried.

## DDO enemy and stage parameters (`src/riftstone/ddo_params.py`)

Seven Dragon's Dogma Online resource types with their own small schema engine (conditional blocks, member
limits on counts, string limits, resource references, interleaved arrays), each schema compared
mechanically with a trace of its DDO.exe loader (types, offsets, magic/version, loops, the fly block,
nested records: all equal). Routed by type id (`params._ddo_param_kind`; four have no magic); YAML tags
`<kind>-ddo/1`. `check_corpus --game ddo --only ddo_params`, 2026-09-25: 2,364/2,364 byte-exact and YAML
byte-exact through `params`.

| Kind | Class | Files | Notes |
|---|---|---|---|
| `cpe` | rCharParamEnemy (`cpe\0`) | 288 | the developers' 48 Japanese field names (English glosses in the YAML); a flying block in 63 files (parse follows mFlgEnemyFly, build refuses a mismatch); `mUnk12C[v-1]` scales by a byte v (1..11) of the attacker (the vocation is a guess) |
| `pep` | rAIPawnEmParam | 273 | what pawns know about an enemy; lists look like bit masks (hex) |
| `prs` | rParentRegionStatusParam | 269 | per-part status; mUnk60..70 are packed into one 64-bit key by the code |
| `osp` | rOcdStatusParamRes | 236 | status ailments, Japanese names (異常名称 counts from 1) |
| `sti` | rStageInfo (`sti\0`) | 479 | stage settings + references (rScheduler, rNavigationMesh, rOccluderEx, rStartPos, rLocationData, rZone); the stage number comes from the file name |
| `sal` | rStageAdjoinList (`SAL\0`) | 457 | adjoining stages; mUnk90 = the stage number in 453 |
| `evtr` | rEventResTable | 362 | event resources as `(type << 32) \| JAMCRC(path)`: 5,213 of 7,474 resolve; the rest are type 0x31897EE8 (not a DDO.exe class) |

Not decoded: rStageAdjoinList2 (`.sal2`, 5 files, loader 0x00AF4FD0). UNKNOWN: every `mUnk` field; what
any value does in game.
## SBC -- collision meshes (`src/riftstone/sbc.py`)

`rCollision` (.sbc), magic `SBC\xff`, version `0x77DF2114` (Online: `0x77DF43D8`, the same layout -- all
4,042 distinct DDO files pass the same checks, `check_corpus --game ddo --only sbc`, so `port` moves a mesh
between the games by that word). There are 1,496 distinct DDDA files: stage
terrain, cell collision and objects. The layout comes from `DDDA.exe`'s own loaders:
- `rCollision::loadCore` 0x010A30D0;
- `cBVHCollision::loadCore` 0x0116D2F0;
- `rCollision::bulkAllocateMemoryAll` 0x0109A290.

Field names come from the PS3 build (`rCollision::Header`, `Triangle`, `MaterialInfo`). Every
file's sections add up to its size exactly (`check_corpus --only sbc`). The stream, in order:

```
0x00  Header 0x50   "SBC\xff", u32 version, u32 space division of parts, of triangles (0 = tree on all),
                    u16 parts, u16 materials, u32 leaves, triangles, vertices, modify ids, MtAABB at 0x30
0x50  u32           tree node memory the loader allocates (fixed by the node counts)
0x54  PartsInfo[]   0x50 each: MtAABB, 3 runtime pointer words, (start, count) of leaves / triangles /
                    vertices at +0x2C, id, 2 words
      tree per part, then the parts tree:
                    "BVHC", u32 0x77B17B24, u32 kind (1 = 4-wide, 0x70-byte nodes; 2 = binary, 0x50),
                    MtAABB root at +0x10, u32 nodes at +0x30 (0x40 bytes), then the nodes
      Triangle[]    0x20: MtFloat3 normal, u16 vert_index[3] (counted from the part's first vertex),
                    u16 material_index, u32 attribute, u8 adjustParam[3], u8 reserved, u32 PhysicsReserved
      Vertex[]      0x10: float3 position, u32 0 (0 on all 1,977,739)
      MaterialInfo[] 0x20: u32 attribute, u32 attr[4], 3 words
      Leaf[]        10 bytes: triangle numbers (0xFFFF = none), u16
```

A 4-wide node is:
- one mask byte, repeated 4 times: the low nibble marks lanes that point at nodes, the high nibble
  lanes that point at leaves;
- u16 child[4] and four `0xCD` fill bytes;
- `minX[4] minY[4] minZ[4] maxX[4] maxY[4] maxZ[4]`.

Measured over all files:
- **Lanes:** 1,365,992 point at nodes, 2,809,627 at leaves and 1,398,881 are empty. Every empty lane
  holds finite, real bounds and child 0; no fill ever sits in a lane.
- **Other tree kinds:** there is no binary tree and no grid in the game. 452 files count modify ids,
  but the loader reads none from the stream.
- **Containment:** every part keeps its vertices inside its box, and its box inside the file's (26,137
  of 26,137). Trees may reach about 1 cm past their part.
- **Online:** revision `0x77DF43D8` (4,042 files) has the same layout and passes the same checks.
- **Triangles:** a triangle's three vertex numbers count from its part's first vertex (a file holds more
  vertices than a u16 names). Read that way (`sbc.triangles`) every one of the 2,920,986 triangles belongs to
  one part, names three of that part's vertices, and its stored normal is its corners' own; none has no area.
  Online's 4,868,199 triangles hold the same (`check_corpus --game ddo --only sbc`).

Moving a mesh (`sbc.translate`, `riftstone terrain`) changes only positions. Triangles store no plane
distance, so they stay as they are. What moves:
- the file box, the part boxes and the tree roots;
- every node lane;
- every vertex.

## NAV -- navigation meshes (`src/riftstone/nav.py`)

`rNavigationMesh` (.nav): where a stage's AI walks. Magic `NAV\0`, version `0x21` in both games: 41 distinct
Dark Arisen files (42 names; st610 and st611 are the same bytes) and 332 distinct Online files, all rebuilt
byte for byte (`check_corpus [--game ddo] --only nav`). Read from `rNavigationMesh::load` 0x01099E00, which
checks the header and calls the body reader 0x01099100, which reads the hierarchy through `rAIPathBase`'s
reader 0x011FB840; `rNavigationMesh::save` 0x01099BA0 writes the same order. The mesh's members are named by
the PS3 build; the node and area structs live in the MT Framework library, which the PS3 build has
no debug records for, so their parts are named after its methods (`nodeData::getNodeAttribute`,
`getNodeArea`, `HierarchyArea::getGeometry`, `getChild`, `getLink`).

A packed little-endian stream, read field by field:

```
mCoreHeader     "NAV\0", u32 0x21, u32 0 (every file), u32 slots of mpNodeBuffer (all triangles' lists)
mName           u32 n, then n characters and a NUL, n <= 255 ("new Navigation" in 38 of 41)
counts          u32 vertices, u32 triangles (mNumberOfNode), u32 node infos (0 everywhere), u8 1 = extras (else 0)
vertex[]        float3 position (cm); with the extras u8 mpNearWall and u16 mpWallDistance (0, 0 everywhere)
triangle[]      s32 own index, u32 n + u32 attributes (one bitfield: 0 on 95%), u8 flag (0),
                float3 (0, 0, 1) and f32 (0) everywhere, u8 n + u32 areas (none), u32 n + s32 corners
                (three vertex indices), u32 n + n links of u32 neighbour, u32 0, u32 edge (0..2),
                f32 cost, f32 0, f32 0
hierarchy       u16 areas; each: u16 id, u32 n + name (as mName), u32, u32 geometries of u8 kind (0 box: float3 min,
                max; 1 oriented box: float3 extent + 4 x float4; 2 sphere: f32 radius, float3 centre;
                another kind: nothing), u16 first triangle, u16 triangles, u16 parent, u8 n + children,
                u8 n + links; then u16 mNumberOfTotalAreaChild, u16 mNumberOfTotalAreaLink
totals          u32 mNumberOfTotalLink, u32 mNumberOfTotalAttribute, u32 mNumberOfTotalIndex
node info[]     u32 + 32 bytes
quadtree        u8 depth (7 everywhere), u32 nodes = (4^depth - 1) / 3 (5,461), float4 min, float4 max,
                then per node u32 n + n x (u32 triangle, u32 0)
```

Measured over every file:
- **Triangles and links:** a link crosses the edge from corner `edge` to corner `edge + 1`; its cost is the
  distance between the two triangles' centroids in metres (centimetres / 100): every one of Dark Arisen's
  305,967 links within 0.1%, and every one of Online's 1,613,727 within 1% but 96 of one mesh (rm107), whose
  corners moved after its costs were baked. All links but one in each game have their reverse.
- **Totals:** the header's slot count equals attributes + areas + corners + 6 x links; the three totals after
  the hierarchy equal the links, attributes and corners. The loader trusts all of them (it allocates by
  them), so `nav.parse` refuses a file where they differ, and a vertex, neighbour or tree entry that points
  past its list.
- **One area everywhere:** `root`, one box around the mesh, triangles 0.., parent 0xFFFF.
- **Names:** the loader does not copy n + 1 bytes: its string reader (0x00CFEE30) reads up to the first NUL,
  whatever n says, and stores at most n characters (`4D 3B FD 73 04 88 04 1F 47` at `0x00CFEE62`, looping
  until the NUL: `84 C9 75 D2` at `0x00CFEE8D`). The buffer is 256 bytes on the reader's stack: the mesh's
  name goes to `esp + 0x60` of a 0x160-byte frame (`81 EC 54 01 00 00` at `0x01099106`, `8D 44 24 64` at
  `0x0109911F`, the call `E8 05 5D C6 FF` at `0x01099126`), an area's to `esp + 0x0C` below a local at
  `+0x10C` (`8D 44 24 10` at `0x011FB8F7`, `E8 2D 35 B0 FF` at `0x011FB8FE`, `8D 8C 24 0C 01 00 00` at
  `0x011FBAE6`). So `nav.parse` takes a name only as at most 255 characters with its one NUL last: a NUL
  earlier would make the loader read every later field from another place, and a longer name overruns the
  buffer.
- **The extras byte:** the loader reads a vertex's near-wall byte and wall distance only when it is 1
  (`80 7C 24 13 01` at `0x010992D6`); every file has 1, and `nav.parse` takes 0 or 1.
- **Numbers:** every float is finite in every file; `nav.parse` refuses any other (a signalling NaN would not
  be written back with the same bits).
- **The game's walkers stand on it:** 2,840 enemy placements, height over the mesh median 0 cm, 95% within
  35 cm (`tools/nav_proof.py`).

**Which mesh a stage loads** is the engine's stage table, not the stage's own name: 65 stage numbers at
0x01530348 and a 22-byte row each at 0x015303D0, whose seventh s16 names the stage of the `_nav` it loads
(read at 0x00503368; the sixth names the merged collision's). Eleven stages borrow: 402 -> 401; 421, 423,
424, 425 -> 420; 431, 435, 436 -> 430; 444, 445 -> 443; 447 -> 446 (`nav.NAV_OF`, compared with the player's
exe by `check_corpus --only nav`). The open field (100), 501 and 703 have none; the field's AI uses waypoint
cells (`.way`, not decoded).

## Files Riftstone writes

| File | Where | What |
|---|---|---|
| `riftstone-arc.json` | unpacked folder | order, exact names, types, flags, SHA-256 per resource |
| `riftstone-mod.json` | mod folder | name, version, author, priority |
| `state.json` | `<game>\riftstone\` | enabled mods, installed archives + hashes, mode |
| `vanilla\…` | `<game>\riftstone\` | verified copies of replaced archives (Online's direct mode; Dark Arisen archives an older Riftstone replaced, until restored) |
| `overlay\…` | `<game>\riftstone\` | built archives served by the loader (overlay mode) |
| `logs\loader.log`, `crash-*.txt/.dmp` | `<game>\riftstone\` | loader activity and crash reports |
| `index-*.sqlite` | `%LOCALAPPDATA%\Riftstone` | resource index |

# Pawn Architect: the party in your save (`riftstone saves pawns`)

The party lives in `DDDA.sav` (`docs/saves.md`): the Arisen, the main pawn and the two hired pawns, in each of the
two copies of the player's data the save holds. `riftstone saves pawns` reads them, puts a copy of your main pawn
into the hired slots, sets any of the four characters' level, vocation, rank, discipline, stats and skills, gives
one character another's looks, and says what the party holds that the game itself would not have made. It writes
the save only with `--yes`, with the game closed, and keeps the save as it was first (`riftstone saves restore`
puts it back). Code: `src/riftstone/pawns.py` (the records), `src/riftstone/saves.py` (the parameter edits and
the vocation's gear check), `riftstone saves pawns` in `cli.py`.

```bat
riftstone saves pawns                          :: the party, in both copies of the player's data
riftstone saves pawns clone --yes              :: your main pawn into hired slots 1 and 2 (close the game first)
riftstone saves pawns clone --slot 2 --yes     :: only slot 2
riftstone saves pawns set --who 1 --level 200 --rank 9 --points 999999 --yes
riftstone saves pawns set --who main --job 9 --yes          :: the main pawn becomes a Sorcerer
riftstone saves pawns looks --from arisen --to main --yes   :: the main pawn gets the Arisen's looks
riftstone saves pawns guard                    :: what the party holds that the game would not have made
riftstone saves arisen --job 7 --yes           :: the Arisen becomes a Warrior
```

## The records (measured on this machine's save, 2026-10-13, read only)

Each copy of the player's data (`mPlayerDataManual`, `mPlayerDataBase`) holds `mPlCmcEditAndParam`, and in it:

| Record | Type | What it is |
|---|---|---|
| `mPl` | `cSAVE_DATA_PL` | the Arisen: `mEdit` (`cSAVE_DATA_EDIT`, the looks and the name) and `mParam` (`cSAVE_DATA_PARAM`, `docs/saves.md`) |
| `mCmc` | 3 x `cSAVE_DATA_CMC` | the pawns in party order: the main pawn (`mPawnType` 1), hired slots 1 and 2 (`mPawnType` 0) |
| `mItem` | 4 inventories | the Arisen's, the main pawn's and each hired pawn's |

A pawn's record holds `mEdit`, `mParam`, its knowledge (`mStudyData`, `docs/saves.md`) and inclinations, then
`mPawnType` (s32), `mEmployGamePlayTime` (u32, the play time at which it was hired) and last `mOnlinePawnInfo`:

| Field | Type | What it holds |
|---|---|---|
| `mNetUniqueId.mData` | u8 [64] | the network id of the player whose pawn it is (`mDataLength` 64) |
| `mEmployCnt` | u32 | how often it was hired |
| `mArisenName` | `cName` | the owner's name (`( u8* )mEditName`, 25 bytes, NUL-terminated) and what pawns call the owner (`mNicknameID`, u16) |
| `mArisenLevel`, `mArisenJob`, `mArisenJobLevel` | u32, s32, u32 | the owner's level, vocation and rank |

Each equipped item (`mParam.mEquipItem`, 12 `sItemManager::cITEM_PARAM_DATA`) names its owner in
`data.mOwnerId` (s8): 0 the Arisen, 1 the main pawn, 2 and 3 the hired pawns, whose items also carry bit 0x80 of
`data.mFlag`. Every item the hired pawns wear in this save's base copy is so; its manual copy's hired slots wear
nothing at all. A pawn of your own carries your network id: the main pawn's `mNetUniqueId` is the id the hired
pawns of other players do not have.

## What each command does

- **list** reads both copies: each character's name, level, vocation and rank, how many of its 12 slots are
  worn, and for a pawn whose it is ("yours", another player's name and level, or "a clone of the main pawn":
  a hired slot carrying the main pawn's non-zero network id).
- **clone** replaces each chosen hired slot's record with the main pawn's record of the same copy, then sets what
  makes it a hired pawn: `mPawnType` 0, the slot's own `mEmployGamePlayTime`, each worn item's `data.mOwnerId`
  to the slot's (2 or 3) with bit 0x80 in `data.mFlag`, and the owner fields from the Arisen of that copy
  (`mArisenLevel`, `mArisenJob`, `mArisenJobLevel`, the name into `mEditName`, `mNickname` into `mNicknameID`;
  refused when the call name does not fit its u16). Nothing outside the slots' records changes, and a second
  clone changes nothing. The hired pawns' own inventories (`mItem`) stay as they were.
- **set** (`--who arisen|main|1|2`) changes values of that character's `mParam` in both copies, as
  `saves arisen` does for the Arisen (`docs/saves.md`): `--level`, `--rank`, `--points`, the stats, `--skills
  all`, and `--job`, the vocation. A pawn can take only Fighter, Strider, Mage, Warrior, Ranger and Sorcerer
  (1, 2, 3, 7, 8, 9); the hybrids are the Arisen's. A new vocation is refused while the character wears an item
  whose `mEnableEquipJob` in the game's item list (bit N = vocation N; `docs/formats.md`) does not let it: take
  that off in the game first. A vocation the character never took holds rank 1, no discipline and 1 to go; taking
  it puts the first rank's cost there, as the game does (Fighter, Strider, Mage, Warrior and Ranger 500, Mystic
  Knight and Assassin 800, Magick Archer 650, Sorcerer 600: `mJobNextExp` of every taken vocation at rank 1 with
  no discipline earned, on the 26 parameter blocks of this machine's save).
- **looks** copies every value of one character's `mEdit` to another's, both copies, except the name
  (`mNameStr`), the call name (`mNickname`) and the personality (`mPersonality`). Both records must hold the same
  fields in the same order.
- **guard** prints, per copy: a level outside 1-200, a vocation outside 1-9 or a pawn in a hybrid, a clone of
  the main pawn, a hired pawn's item not marked as its slot's, and an item its wearer's vocation cannot wear (when
  the game is found). It exits 1 when it found anything. It hides nothing: the game, Steam and the pawn servers
  see the save as it is, so before playing online put back a copy from before the changes (`riftstone saves
  list`, `riftstone saves restore N`). Riftstone makes no claim about the online pawn system's checks.

Measured on this machine's save in memory (2026-10-13; the live file was not written): clone replaced 4 records
(two slots in two copies), the result checks all the way through, every byte outside the four records is the
same, a second clone changes nothing, the party reads the slots as clones of the main pawn, and guard went from
nothing to four lines. `set --who main` was refused `--job 7` and `--job 9` for items the main pawn wears and
`--job 5` as a hybrid, the Arisen `--job 7` for four worn items; `looks --from arisen --to main` changed 56 values
and kept the pawn's name.

**UNKNOWN until observed in game:** whether the game shows a clone in a hired slot (the hired slots' presence flag
is not decoded: `mCmcBeFlag` is false in every record of this save, with a party of four), which copy of the
player's data the game loads, what the Rift and the pawn servers do with a clone, and everything the set and looks
commands change on screen.

## Proof

- `tests/test_pawns.py`: a stand-in save built like the game's (both copies, the Arisen and three pawns with
  their gear, owners and network ids, and a pawn record in the system data nothing touches): the party read;
  refusals (more than three pawns, a main pawn's `mPawnType`, a record without its `mParam` or `mEdit`, unbalanced
  XML); clone into both slots and one (every changed value named, bytes outside the slots the same, idempotent);
  set (only the named character's values, the vocation's gear, a hybrid pawn, the first rank's cost); looks (the
  name kept, a field missing refused); guard; the CLI (`--yes` required, a copy kept per write).
- `fuzz/targets.py` `save_pawns`: under random saves each change refuses with RiftError, or changes only what it
  names (a clone: only the chosen slots, each the main pawn's record with only values changed), writes values as
  the game writes them, still reads, and changes nothing when repeated. Its first run found a field name that is
  not UTF-8 crashing `looks` (now refused; a regression test in `tests/test_pawns.py`).

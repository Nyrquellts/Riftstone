# Where an enemy's health is

Short answer, for Dark Arisen:

- **A monster's base health is `mHPMax` in its `.rst` file**: `charparam\em\em<id>.rst`, an `rRegionStatus` (the
  Goblins' is `em0100.rst`; a variant has a suffix, `em5200_00.rst`). It is not in the `.prp`, which carries attack,
  defence, resistances, scale and EXP.
- **A human enemy** (Bandits, Adventurers, Soldiers, the cult and guild members, Assassins: `em1000`..`em1007`) has no
  `.rst`. Its health is the `.prp`'s `人間敵 HP` ("Human-enemy HP"): one `.prp` per variant and level
  (`em1000_00_2_cmn` .. `em1000_08_0_cmn`, 38 files, 1,000 to 35,000), picked by a table in the executable.
- **One placement** can scale either again: a placement in a `.lot` has `HP倍率設定の有無` (a flag) and `HPの倍率`, and
  the game multiplies the first region's health by it while the flag is on (`Riftstone.cmd spawns`, Studio's map).

Everything below is read from the files and from the executable; `tools/enemy_hp_proof.py` repeats the measurements and
the gate re-proves the bytes quoted. What an edited number does in play is UNKNOWN until someone plays it (the last
section lists what else is).

## Change a monster's health

```bat
Riftstone.cmd find goblin
Riftstone.cmd new "Tougher Goblins"
Riftstone.cmd extract charparam/em/em0100.rst --mod "Tougher Goblins"
Riftstone.cmd install "Tougher Goblins"
```

`find goblin` lists `charparam/em/em0100.rst` right after the stats. Before `install`, open
`files\charparam\em\em0100.rst.yaml` in the mod's folder and change the first `mHPMax`:

```yaml
mRegionStatusList:
  - mNo: 0
    mType: 0
    mElementList:
      - mNo: 0
        mId: 0
        mHPMax: 1000.0        # the Goblin's base health: change this one
```

`riftstone build "Tougher Goblins"` writes the archive without touching the game; the built `.rst` differs from the
game's in that one number and nothing else. A human enemy is the same with a `.prp`: `Riftstone.cmd extract
charparam/em/em1000_00_2_cmn.prp`, then `人間敵 HP`. Which of the 38 files a placed human uses is decided in the
executable (below); change every one of them to change them all.

## The file

`rRegionStatus` (magic `0x20110930`, `flat.py`): a list of `cRegionStatus` (`mNo`, `mType`, then `mElementList`), each
element 56 bytes: the fourteen 4-byte values below, in this order. The names are the executable's own; the offsets are
the engine's in-memory ones (`+0` is the vtable), which is why they do not run in file order.

| YAML field | Element offset | Where `initRegionStatus` puts it in the region | Reading |
|---|---|---|---|
| `mNo` | +0x04 | | the element's number in its list |
| `mId` | +0x08 | +0x04 | an id |
| `mHPMax` | +0x0C | +0x08 (`mHP`) and +0x0C (`mHPMax`) | **health** |
| `mDPMax` | +0x10 | +0x10 and +0x14 | engine name; what it does in play is not measured |
| `mDPSpeed` | +0x14 | +0x18 | the same |
| `mBPMax` | +0x18 | +0x1C and +0x20 | the same |
| `mBPSpeed` | +0x1C | +0x24 | the same |
| `mDamageAdj` | +0x20 | +0x28 | a damage adjustment, by its name |
| `mHitStopAdj` | +0x24 | +0x2C | a hit-stop adjustment, by its name |
| `mSurface` | +0x2C | +0x1BC | |
| `mSeSurface` | +0x30 | +0x1E4 | |
| `mAttr` | +0x28 | +0x30 | |
| `mDPResetTimerMax` | +0x58 | +0x3C | |
| `mBPResetTimerMax` | +0x5C | +0x48 | |

The engine's editor-only `mComment` (+0x34) is not in the file. `mType` picks a set of regions: the callers ask for
type 0 almost everywhere, and `initRegionStatus` takes the first set of the type asked for and stops, so a second type
0 set in a file (ten files have one) is not read by it.

Measured on the installed game (2026-10-02, `tools/enemy_hp_proof.py`):

- 106 `.rst` resources; all 106 parse and rebuild byte for byte (`check_corpus --only flat`), and every one has a type
  0 set (116 of them).
- Set types present: 0 (116 sets in 106 files), 1 (66), 2 (40), 3 (33), 4 (25), 5 (16), 6 (7), 7 (8), 8 (1), 10 (43).
  What the types other than 0 are for is UNKNOWN.
- The first element of the first type 0 set: 1 at least, 7,750 in the middle, 1,470,000 at most. By the game's own
  names: Goblins 1,000, Hobgoblins 2,000, Greater Goblins 14,500, Wolves 800, Harpies 800, Ogres 20,000, Chimeras
  30,000, Hydras 100,000, The Dragon 100,000, Death 660,000, The Ur-Dragon 1,470,000; the rabbits, rats, crows, snakes
  and spiders have 1. A file's later elements are other parts of the same creature (the Goblins' two are both 1,000,
  the Chimeras' six are 30,000, 15,000 and four of 5,000, the Dragon's fourteen are mostly 100,000).
- 88 of the 105 enemy families in the game's name tables have an `.rst` (87 `em<id>.rst`, and the Maneaters' is
  `em5503_00`; the Chimeras, Hydras, Evil Eyes and Gazers also have `_00`/`_01` variants). The other 17 are the eight
  human families and nine that have a model and no parameter file (`em0300`-`em0303`, `em0800`, `em0801`, `em5700`,
  `em5701`, `em9807`).
- The `.prp`'s `人間敵 HP` is 1,000 in 84 of the 85 monsters that have both files (2,200 in the Skeleton Sorcerers), and
  equals the `.rst`'s health in 4 of them, while their `.rst` health runs from 1 to 1,470,000: in a monster it does not
  follow the creature's strength.
- The human enemies' 38 `.prp` files (`em1000_<variant>_<n>_cmn`) all carry it, 1,000 to 35,000, 23 distinct values.

## Evidence (DDDA.exe, build 2364871)

### The names

`cRegionStatusElement` registers its fields in one function, `0x00CDA9A0`..`0x00CDAD6C`. Each registration loads the
member's address (`lea reg, [edi + offset]`), then pushes the field's name and its kind (`0xC` is a 32-bit float):

| Evidence | Address |
|---|---|
| `mHPMax` at element offset `0x0C`: the `lea ecx, [edi + 0xC]`, then the name `0x016198B4` and kind `0xC` | `8D 4F 0C 8D 54 24 10 89 4C 24 1C 52 8B CB C7 44 24 14 B4 98 61 01 C7 44 24 18 0C 00 00 00` at `0x00CDAA31` |
| `"mHPMax"` | `"mHPMax"` at `0x016198B4` |
| `"mDPMax"`, `"mBPMax"`, `"mDamageAdj"`, `"mHitStopAdj"` | `"mDPMax"` at `0x01619F94`, `"mBPMax"` at `0x01619FA8`, `"mDamageAdj"` at `0x01619FBC`, `"mHitStopAdj"` at `0x01619FC8` |
| `"mSurface"`, `"mSeSurface"`, the two timers | `"mSurface"` at `0x01619FD4`, `"mSeSurface"` at `0x01619FE0`, `"mDPResetTimerMax"` at `0x01619FEC`, `"mBPResetTimerMax"` at `0x0161A000` |
| the list names | `"mRegionStatusList"` at `0x01619F60`, `"mElementList"` at `0x01619F84` |

### From the file to the enemy

The executable keeps, for every enemy, the list of resources it loads: entries of three words in its data, the
pointer to the resource's name, the pointer to its class record and a 0. The Goblins' list names
`charparam\em\em0100_cmn` as an `rPropParam` (class record `0x018D2C40`) and right after it `charparam\em\em0100` as an
**`rRegionStatus`** (class record `0x019A909C`):

| Evidence | Address |
|---|---|
| the Goblins' `.prp` entry: name `0x015A34B0`, class `0x018D2C40` | `B0 34 5A 01 40 2C 8D 01 00 00 00 00` at `0x0183767C` |
| their `.rst` entry: name `0x015A349C`, class `0x019A909C` | `9C 34 5A 01 9C 90 9A 01 00 00 00 00` at `0x01837688` |
| the name | `"charparam\\em\\em0100"` at `0x015A349C` |

126 such entries in the executable name an `rRegionStatus` (the Goblins' list is there twice, `0x01837B60`): 93 are
`charparam\em\em<id>` and 13 are variants (`em5001_prison`, `em5101_00`, `em5200_00`, `em5200_01`, ...). Their 106
distinct names are exactly the 106 `.rst` resources in the game's archives, no more and no fewer. The lists also name
147 of the 185 enemy `.prp` files; the 38 they do not name are the human enemies' (below).

The enemy's resource router (`0x00889510`, called that here for what it does) is handed each loaded resource, tests its
class against the ones it knows, and for an `rRegionStatus` calls the setter at `0x0076DA40` with the resource in `edi`
and the `cObjCollision` that sits inside the enemy (`+0x10E0`) in `esi`. The setter keeps it in that object's `+0xCF8`
(the enemy's `+0x1DD8`), where `initRegionStatus` reads it:

| Evidence | Address |
|---|---|
| the class test loads the name word of class record `0x019A909C` (`+4`) | `8B 0D A0 90 9A 01` at `0x00889844` |
| the hand-over: the resource in `edi`, the `cObjCollision` in `esi`, the call to `0x0076DA40` | `8B 7D 08 8D B3 E0 10 00 00 E8 D0 41 EE FF` at `0x00889862` |
| the setter stores it at `+0xCF8` | `89 BE F8 0C 00 00` at `0x0076DA62` |
| `initRegionStatus` reads it from there | `8B 8F F8 0C 00 00` at `0x00770916` |

(The Gorecyclops' own code also loads `em5001` or `em5001_prison` by name as an `rRegionStatus`, at `0x0097E916` and
`0x0097E9F4`, and stores it in the same field.)

### From the file to a region

`cObjCollision::initRegionStatus` (`0x00770910`; the type in `eax`, the `cObjCollision` on the stack) walks the
file's list for the first `cRegionStatus` whose `mType` equals the type, and for each of its elements makes a
0x1E8-byte region and copies the element into it, then returns without looking further:

| Evidence | Address |
|---|---|
| the type test, `cmp [ebx+8], esi`, and the jump out on the first match | `39 73 08 74 0B 40 3B C2 72 E9` at `0x00770948` |
| the region is 0x1E8 bytes | `68 E8 01 00 00` at `0x0077098F` |
| `mHPMax` into the region's `mHP` and `mHPMax` | `D9 40 0C D9 5E 08 D9 40 0C D9 5E 0C` at `0x007709C4` |
| `mDPMax` into +0x10 and +0x14 | `D9 40 10 D9 5E 10 D9 40 10 D9 5E 14` at `0x007709D0` |
| `mSurface` into +0x1BC | `8B 50 2C 89 96 BC 01 00 00` at `0x00770A00` |
| the two reset timers | `D9 40 58 D9 5E 3C D9 40 5C D9 5E 48` at `0x00770A28` |
| after the matched set's elements it returns | `5D 5B 5F 5E C2 08 00` at `0x00770A7D` |

86 places call it; 78 load the type 0 first, and most of them hand it the `cObjCollision` that sits inside an enemy,
whose region count and array are the enemy's `+0x1D0C` and `+0x1D18`. What the game reads as an enemy's health is the
first region's `+8`:

| Evidence | Address |
|---|---|
| `getHp`: the region count, the array, the first region, its `+8` | `83 B8 0C 1D 00 00 00 0F 57 C0 76 11 8B 80 18 1D 00 00 8B 00 85 C0 74 05 F3 0F 10 40 08 C3` at `0x00410400` |

So a monster's health is the first element of its first type 0 set, as loaded, before any multiplier.

### The placement's multiplier

`cSetInfoPawn::applyInfo` (`0x007A4B80`) copies each stat multiplier's flag and value from the placement into the
enemy; the health pair goes to `+0x209E` (a byte) and `+0x20A0` (a float), and a function at `0x00889040` multiplies the
first region's `mHP` and `mHPMax` by it while the flag is set:

| Evidence | Address |
|---|---|
| the placement's HP flag and value copied into the enemy | `0F B6 47 74 88 86 9E 20 00 00 D9 47 7C D9 9E A0 20 00 00` at `0x007A4BC9` |
| the flag is tested | `80 BE 9E 20 00 00 00` at `0x00889043` |
| the first region's `mHP` times the value | `F3 0F 10 48 08 F3 0F 59 C8 F3 0F 11 48 08` at `0x00889069` |
| the first region's `mHPMax` times the value | `F3 0F 10 48 0C F3 0F 59 C8 F3 0F 11 48 0C` at `0x00889094` |

Eight more places test the flag, each in the setup of a class that sets its own health: the Goblins (`0x008CDE91`), the
Chimera's body and its two part classes (`0x009A12AD`, `0x009B15C6`, `0x009B94C3`), the Evil Eyes (`0x00A07536`), the
Gazers (`0x00A0E51B`), the human enemies (`0x00BA7548`) and the function behind `0x00C53C1C` (map objects). They were
found by scanning for the flag's offset and read in their setups; the Chimera body also hands the flag and value on to
its parts.

### Human enemies

The human enemies are one class, `uHumanEnemy` (`0x7400` bytes), and no resource list names an `.rst` for it. Its
`setup` (`0x00BA74C0`) calls `initEmCommonParam` (`0x00BA9400`), which walks a table of 92 rows of 12 bytes (two numbers
and a name) at `0x014EEEA0`, compares the rows' numbers with two fields of the unit (`+0x354C` and `+0x3070`), and loads
the `.prp` the first matching row names as an `rPropParam`; with no match it loads `em1000_00_2_cmn`. The 92 rows name
38 different files, all of `em1000_<variant>_<n>_cmn`. The loaded `.prp` is copied into a `cCharParamEnemy` the unit
embeds at `+0x7170` and from there into a second at `+0x72B0`. `人間敵 HP` is that class's field at `+0xDC`, the unit's
`+0x724C`; "Human-enemy flinch endurance" is `+0xE0` (the unit's `+0x7250`) and "Human-enemy knockback endurance" `+0xE4`
(`+0x7254`):

| Evidence | Address |
|---|---|
| the table's start | `B8 A0 EE 4E 01` at `0x00BA940D` |
| its length, 0x450 bytes = 92 rows | `81 F9 50 04 00 00` at `0x00BA9429` |
| the unit's first number, compared with each row | `8B 93 4C 35 00 00` at `0x00BA9405` |
| the fall-back name | `"charparam\\em\\em1000_00_2_cmn"` at `0x015EEC24` |
| the copy of `人間敵 HP` between the two `cCharParamEnemy` | `D9 82 DC 00 00 00 D9 98 DC 00 00 00` at `0x0074A086` |
| `setup` reads the health from the unit's `+0x724C` | `F3 0F 10 8E 4C 72 00 00` at `0x00BA754F` |
| it stores region 0's `+8` | `F3 0F 11 45 08` at `0x00BA75DC` |
| and its `+0xC` | `F3 0F 11 40 0C` at `0x00BA75FD` |
| every region's DP from the flinch endurance (`+0x7250`) | `D9 86 50 72 00 00 D9 58 10` at `0x00BA7625` |

Between the read and the stores `setup` multiplies the health by the placement's value when the flag is set (the test
at `0x00BA7548`, above).

### A second region creator

`0x00770A90` builds the same kind of region from a compact parameter block instead of an `rRegionStatus`, with the
health at `+4` of the block. It has three callers in the player and pawn setup (`0x00B5ECD0`) and one in a function for
map objects (`0x00C53B70`), which applies the placement's ratio at `0x00C53C1C`:

| Evidence | Address |
|---|---|
| the block's health read | `F3 0F 10 47 04` at `0x00770ACF` |
| stored as the region's `+8` and `+0xC` | `F3 0F 11 46 08 D9 5E 28 F3 0F 11 46 0C` at `0x00770AE8` |

### The PS3 build

The PS3 build's names agree: its `cObjCollision::initRegionStatus`, `cRegionStatusElement` and `mHPMax` are the same
classes and fields, it scales the health by the placement's ratio the same way, and the human enemies' three values are
named `mHumanEnemyHp`, `mHumanEnemyDp` and `mHumanEnemyBp` there. (Guidance only; every address on this page is the
PC executable's.)

### How to find it again

Search the executable for the property names (`mHPMax` is the one whose registration loads an address at offset `0xC`
and a float kind), then for the x87 copy `fld [x+0xC]; fstp [y+8]; fstp [y+0xC]` (only `0x007709C4` has it), then for
whoever reads `[y+8]` of the first region (`getHp`, and its inlined copies). To get from a file to a creature, search
the data for the class record's address (`9C 90 9A 01`) with a pointer to a `charparam` name just before it. A second
class registers an `mHPMax` as well, a 16-bit field between `mLevel` and `mStaminaMax` (registered near `0x00CD669C`);
it is not the one the monsters use.

## UNKNOWN

- That an edited `.rst`, or a human enemy's `.prp`, changes the health in play. The chain above is static: file,
  resource list, router, `initRegionStatus`, region, `getHp`. Nothing here has been played.
- The code that walks a resource list and hands each resource to the router was not traced. The name sets matching
  exactly is why this page takes the lists to be the path; a resource the router gets some other way would not show.
- Where the human enemies' region 0 is created (their `setup` writes into it), what a placement's `mHumanEnemyKind` and
  `mHumanEnemyID` do to the unit's two fields `+0x354C` and `+0x3070`, and so which of the 38 files a placed human
  uses.
- What the later elements of a set do (the Goblins' second element, the Chimeras' parts), what DP and BP are in play,
  what `mDamageAdj` and `mHitStopAdj` multiply, and what the sets of types 1-8 and 10 are for. Eight of the 86
  `initRegionStatus` callers load the type from somewhere this page did not trace.
- Whether the game scales health by level, difficulty or party beyond the placement's ratio: the paths read show none.
- Online (DDO): its client multiplies an enemy's health by a percentage from `named_param.ndp` (`docs/ddo-solo.md`);
  where its base health lives is not decoded here.

# Solo Balance: Dragon's Dogma Online for one player with pawns

Dragon's Dogma Online was balanced for parties of four players (content parties up to eight). Played alone on the
local Arrowgene server, one player and three pawns face the same enemies. **Solo Balance** is a DDO mod that
Riftstone writes from the game's and the server's own files: every enemy gets lower HP (bosses and extreme missions
also lower attack) when the party has exactly one real player, and the server's pacing settings are raised a
little (EXP, job points, play points, gold, rift points ...). Parties of two or more players play the original
game.

```bat
Riftstone.cmd ddo solo --mod "%USERPROFILE%\Documents\Riftstone\mods\Solo Balance"
Riftstone.cmd install "%USERPROFILE%\Documents\Riftstone\mods\Solo Balance" --game ddo
<path> server stop        :: then Play Solo.cmd (the server reads its files when it starts)
```

To take it out: `Riftstone.cmd uninstall "%USERPROFILE%\Documents\Riftstone\mods\Solo Balance" --game ddo`, then
restart the server. `Riftstone.cmd restore --game ddo` takes out every Riftstone mod at once. Both put back the
original archives and server files, checked by hash.

The code is `src/riftstone/ddo_solo.py`; the named-param format is the `ndp` kind of `src/riftstone/ddo_params.py`.

## Why it works this way

The server never computes or sends enemy HP. For every spawn it sends an enemy id, a level, a scale and one number
that matters here: **NamedEnemyParamsId**. The client looks that id up in `param/named_param.ndp` (rNamedParam, in
`rom/game_common.arc`) and multiplies the enemy's stats by the record's rates, in percent. The server keeps a copy of
the table (`Files\Assets\named_param.ndp.json`) and reads only the EXP rate from it. A server script
(`scripts\enemies\instance_properties\*.csx`, called each time the server makes an enemy for a party) can change the
id.

So the mod gives every record **solo twins**: the same record under a new id with lower HP (and attack) rates, one
per tier. A server script swaps an enemy's id to its twin when the party has one player.

## What the mod holds

| File in the mod | Goes to | What it is |
|---|---|---|
| `files/param/named_param.ndp` | `rom/game_common.arc` | the 2,366 records, then 7,098 twins (9,464 records, 499 KB) |
| `files/ui/00_message/named/named_param.gmd` | `rom/ui/gui_cmn.arc` | a label `namedparam_<twin id>` per twin with the original's text, so a twin shows the same name ("Vigilant Goblin") |
| `server/named_param.ndp.json` | the server's `Files\Assets` | the same twins for the server (written in the file's own Jackson style) |
| `server/scripts/enemies/instance_properties/solo_balance.csx` | the server | the script that picks twins |
| `server/scripts/settings/GameServerSettings.csx` | the server | the server's own template with ten values changed |
| `server/scripts/settings/PointModifierSettings.csx` | the server | the server's own template with three values changed |
| `solo-balance.json` | (not installed) | what was made, from which files (sha256), with which options |

Each settings file starts with a comment listing its changes and why.

## The tiers

The server script decides the tier from the spawn, after the server's own property scripts have run
(`ScriptRank` 1000; `bloodorbs.csx` and `the_rift.csx` have rank 1):

1. **Extreme mission**: the enemy belongs to an extreme-mission quest (quest id 50,000,000-59,999,999, the server's
   own `QuestManager.IsExmQuest`) or has a raid boss id.
2. **Boss**: `IsBossGauge` or `IsAreaBoss` (a quest's `is_boss` and a script's `SetIsBoss(true)` set `IsBossGauge`).
3. **Field**: everything else.

One main quest (`q00030120.csx`) marks two enemies with `SetIsBossGauge(true)`, which the server ignores
(`InstancedEnemy.SetIsBossGauge` assigns the property to itself), so they have no boss gauge and count as field
enemies.

| Tier | Twin id | HP (`mHpRate`, and `mHpSub`, the sub parts' HP) | Attack (`mAttackBasePhys`, `mAttackWepPhys`, `mAttackBaseMagic`, `mAttackWepMagic`) | Twin ids |
|---|---|---|---|---|
| field | id + 4000 | x0.85 | unchanged | 4047..7250 |
| boss | id + 8000 | x0.55 | x0.85 | 8047..11250 |
| exm | id + 12000 | x0.40 | x0.80 | 12047..15250 |

Why these numbers: a solo party is one player and three pawns. If a pawn does about half a player's damage, the
party does about 2.5 players' damage against content tuned for 4 (bosses) or 8 (extreme missions): 2.5 / 4 = 0.62,
2.5 / 8 = 0.31. The defaults sit near those estimates (bosses 0.55, missions 0.40), with a little less attack because
a lone player draws every attack. Field enemies were already fought with pawns in the original game; they lose 15 %
HP for the missing players' damage. The pawn figure is a guess, so every factor is an option (below).

Rounding: a rate is scaled and rounded half up (212.5 -> 213); a rate of 0 stays 0, anything else stays at least 1.
Everything else in a twin is its original's: type, EXP rate, defence, guard, endurances, ailment damage. EXP is
therefore unchanged: the server pays `exp * (EXP rate / 100)` (whole-number division) from the twin, which has the
original's rate.

Examples (`riftstone open param/named_param.ndp --game ddo` shows every record):

| Record | HP | Attack (base phys) | Field twin | Boss twin | EXM twin |
|---|---|---|---|---|---|
| 2298 (the server's default, on 8,477 of 9,222 spawn rows) | 100 | 100 | 6298: HP 85 | 10298: HP 55, attack 85 | 14298: HP 40, attack 80 |
| 47 ("Training") | 250 | 80 | 4047: HP 213 | 8047: HP 138, attack 68 | 12047: HP 100, attack 64 |

What uses which tier on this server (measured on its files): spawn rows 8,713 field and 509 boss (508 with a boss
gauge, 68 area bosses); quest files 3,493 field, 712 boss and 222 extreme-mission enemies in 17 extreme missions.
Quest scripts (`.csx`) add more; the script tiers them the same way at run time.

## The server settings

Copied from the server's own templates (`scripts\settings\templates`, which the server writes at every start),
with only these values changed. When you have your own `scripts\settings\GameServerSettings.csx` (or
`PointModifierSettings.csx`), the mod starts from yours instead and changes only these values.

| Setting | Default | Solo Balance | Why |
|---|---|---|---|
| `EnemyExpModifier` | 1 | 1.5 | kills come slower with one player's damage |
| `QuestExpModifier` | 1 | 1.5 | the same for quest rewards |
| `JpModifier` | 1 | 1.5 | job points come from the same kills |
| `PpModifier` | 1 | 1.5 | play points likewise |
| `GoldModifier` | 1 | 1.5 | no party to split costs with: repairs, crafting, pawns |
| `RiftModifier` | 1 | 2 | offline nobody rents your main pawn, which was the main rift point income |
| `BoModifier` | 1 | 1.5 | blood orbs: slower solo farming |
| `ApModifier` | 1 | 1.5 | area points gate area ranks; solo clears come slower |
| `EnableMainPartyPawnsQuestRewards` | false | true | the main pawn is the solo player's permanent partner; it earns quest EXP and JP too |
| `AdditionalProductionSpeedFactor` | 1 | 0 | crafting finishes at once: the server multiplies each craft's time (at least 30 s after the pawns' speed skill) by this, so a craft is done at the next once-a-second check. Nothing divides by the result (checked in the server's source: `CraftManager.CalculateRecipeProductionSpeed`, `CraftSkillAnalyzeHandler`, `CraftGetCraftProgressListHandler`). The pawns' quality and quantity skills still count; the client may show its own estimate until it asks again. `--set AdditionalProductionSpeedFactor=1` keeps the timers |
| `AdjustPartyEnemyExpTiers` | spread 0-2: 100 %, 3-4: 90, 5-6: 80, 7-8: 60, 9-10: 50, more: 0 | 0-4: 100 %, 5-8: 90, 9-12: 80, 13-16: 70, 17-20: 60, more: 0 | offline the only support pawns are your other characters' and the server's official pawns, whose levels rarely match yours; the default gives 0 EXP past a 10-level spread |
| `PawnCatchupMultiplier` | 1.5 | 2.0 | a main pawn behind its owner catches up twice as fast |
| `PawnCatchupLvDiff` | 5 | 3 | catch-up starts 3 levels behind |

Unchanged on purpose: `HoModifier` (high orbs), `BBMEnemyExpModifier` (Bitterblack Maze has its own balance),
`NormalPartySize`, everything else.

## Tuning

Run `ddo solo` again with options, then `install` again and restart the server. A run replaces only the files it
made (and removes its own files an option turned off); anything else you put in the mod stays.

```bat
Riftstone.cmd ddo solo --mod "...\Solo Balance" --boss-hp 0.6 --exm-hp 0.5 --exm-attack 0.9
Riftstone.cmd ddo solo --mod "...\Solo Balance" --tiers boss,exm          :: field enemies as they were
Riftstone.cmd ddo solo --mod "...\Solo Balance" --set EnemyExpModifier=2 --set HoModifier=1.5
Riftstone.cmd ddo solo --mod "...\Solo Balance" --no-settings             :: enemies only, the server's pacing
Riftstone.cmd ddo solo --mod "...\Solo Balance" --keep-part-hp            :: body parts keep their HP
Riftstone.cmd ddo solo --mod "...\Solo Balance" --dry-run                 :: show, write nothing
```

`--field-hp --field-attack --boss-hp --boss-attack --exm-hp --exm-attack` take factors from 0.05 to 5; a tier whose
factors are all 1 gets no twins. `--offsets 4000,8000,12000` moves the twin ids (the command refuses offsets whose
ids would meet, and a largest id the client's table cannot hold). `--set NAME=VALUE` changes any number or true/false
setting either template declares. From Python: `riftstone.ddo_solo.generate(game, index, mod_folder, tiers=...,
parts=..., settings=...)`; `plan()` and `write()` separately.

A run always starts from the game's and the server's own files: while the mod is installed it reads the originals
Riftstone kept (`<client>\riftstone\vanilla`, `<client>\riftstone\server-vanilla`), so a table is never twinned twice.

## How the client finds a named parameter (the evidence for appending)

Static reading of DDO.exe 03.04.003 (the unpacked dump in `<path>`, the same
build; `ddon re dis`), addresses as in the running game:

- **The file.** `rNamedParam::vf10` (0x00AB3D50) reads a u32 version and compares it with slot 21 (returns 5), then
  slot 22 (0x00AB3DC0) reads a u32 count, allocates count x 0x3C bytes and calls slot 15 (0x00AB3E20) per record:
  u32 `mID`, u32 `mType`, u32 `mHpRate`, then 21 u16 rates in the order of `NDP_RATES`, each through its setter into
  members +0x04..+0x38 (cNamedParam, 0x3C bytes). A stored `mHpRate` of 0 becomes 1 (0x00AB3FE5). Slots 16-19 return
  record i (18 and 19 check i against the count at +0x6C); slot 20 is the count.
- **The id table.** `aGame::vf06` calls 0x00BFCEA0 (at 0x00406529), which loads `param\named_param` into `sSetManager+0x430`,
  takes the largest id, allocates `(largest + 3) & ~3` slots of 4 bytes at `+0x434` (count at `+0x438`), fills each
  with {0xFFFF, 0}, then for record i stores {i, the message index of the label `namedparam_<id>`} at slot `id`
  (0x00BFD003 formats the label; 0x014F9790 looks it up in `ui\00_message\named\named_param.gmd`, held at
  `sGUI+0x1884`).
- **The lookup.** 0x00BFC0B0 (called from 0x00C3E3CF and 0x00C41ACF, which store the record at `uCharacter+0x1DE4`
  and the id at `+0x1DE0`): id > slot count -> none; slot's record index with bit 15 set -> none; index >= record
  count -> none; else record i. It is an index by id, not a search.
- **The name.** 0x00BFC100 returns the record's type and the slot's message index; the GUI (0x00B79C00) puts the
  text before the enemy's name (type 2), after it (3), instead of it (4), or not at all (1). A missing label leaves
  message 0 ("----").
- **The stats.** `cpEmParamCtrl::vf00` multiplies base HP by `mHpRate` x 0.01 (0x0090404A; its other branch, for a
  sub part, uses `mHpSub`) and base physical attack by `mAttackBasePhys` x 0.01 (0x0090461D), and so on.
- Nothing compares the id with particular values except `id > 1` ("is named", `cpEnemyReact` at 0x006122B1).

What follows: new records can go anywhere in the file with new ids, and the lookup finds them. The limits are fewer
than 0x8000 records (the index is a u16 whose bit 15 means none), fewer than 0x8000 name messages (read with
`movsx`), and **the largest id must not be a multiple of 4**: the table has `(largest + 3) & ~3` slots, so a largest
id divisible by 4 has no slot (the loader writes one past the end). With the default offsets the largest id is 15,250
(15,250 mod 4 = 2), 9,464 records and 9,465 messages. A twin also needs its own name label, which the mod adds.

Repurposing records nothing uses was not needed: appending is safe, and it leaves every original record as it was
(the server's spawn table and quest files use 291 distinct ids, two of which the table lacks and the server replaces
with 2298; quest scripts name more ids in code, so "unused" could not be proven anyway).

## The server side, in detail

- The script counts real players with `client.Party.Clients` (pawns are party members, not clients), then looks the
  twin up in the server's table (`LibDdon.Assets.NamedParamAsset`) and uses it only when it exists and matches the
  original in type, EXP and every rate it does not scale. An id outside 47..3250, or without a matching twin, is left
  alone.
- The server creates a party's enemies when a party member first enters an area (`InstanceGetEnemySetListHandler`)
  and keeps them until the area resets, so a second player who joins meets the solo twins the first player's enemies
  already have until then; enemies made after the join are the originals.
- `bloodorbs.csx` (off by default: `EnableRandomizedBoEnemies`) upgrades only enemies that still have the default id
  2298; Solo Balance runs after it, so the two work together.
- EXP schemes: "Tool" (the default) pays by the record's EXP rate, which twins keep; "Automatic" and "Exm" do not read
  the record at all.

## Other mods and tools

- Another mod that changes `param/named_param.ndp`, `named_param.gmd`, `named_param.ndp.json` or the two settings files
  clashes with this one: the later one wins that file whole (`install` names the clash).
- The English text build (`ddon text build`) does not touch `named_param.gmd`, but `ddon text install` refuses to
  write `rom/ui/gui_cmn.arc` while Solo Balance has changed it. Bring the English build in as a Riftstone mod instead
  (`riftstone import <path><stamp> --mod "DDO English" --game ddo`): the two merge in that archive.
- `ddon verify crc` lists `rom/game_common.arc` and `rom/ui/gui_cmn.arc` as changed while the mod is installed.

## What is measured, and what is not

Measured (2026-09-25, client 03.04.003, the 2026 server build):

- `param/named_param.ndp` rebuilds byte for byte, binary and YAML; its 2,366 records equal the server's
  `named_param.ndp.json` field for field (56,784 fields, 0 differ); every id has its `namedparam_<id>` label
  (`python tools/check_corpus.py --game ddo --only ndp`).
- The twins stay within the client's limits (9,464 records, largest id 15,250, 9,465 messages); the twinned files
  parse; the client's and the server's twins agree field for field (`tests/test_ddo_solo.py`, the corpus test).
- Installed with `riftstone install`: only the two resources changed in the two archives; the kept originals match
  the distribution's CRC32; the four server files are in place (`riftstone status --game ddo`: no drift).
- The server started with the mod (`ddon server start`), compiled `solo_balance.csx`, `GameServerSettings.csx` and
  `PointModifierSettings.csx` with no "Failed to compile" line, read `named_param.ndp.json`, and listened on all three
  ports; it stopped cleanly (exit code 0).
- The id lookup, the id table, the name label and the stat multipliers: read from the code (above), not observed.

- The installed files are well formed where the game reads them: `check_corpus --game ddo --only ndp,ddo_params,gmd`
  passes on the live archives (the twinned table and text file round-trip; all 5,686 text files byte-exact). No
  shipped text file has as many labels as the twinned one (9,465; the most is `named_param.gmd` itself with 2,367,
  then 1,987), though the client already loads one with 21,505 messages and 401 KB (`item_name.gmd`, unlabelled).

**UNKNOWN until played:** everything in game. Whether solo enemies have the twin's HP and attack, whether twins show
their names, whether the larger `named_param.gmd` (449 KB instead of 112 KB) or table loads without trouble, which
body parts or sub units `mHpSub` covers (read from its name and the branch that uses it), and whether the pacing feels
right. The static reading covers the unpacked dump of the same build; code that Themida may still hide in the
original was not read.

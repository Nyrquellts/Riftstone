# Shops, gold, Rift Crystals and drops (`economy` plugin)

The `economy` plugin rebalances Dark Arisen's money and loot. It multiplies what shops charge and pay,
the gold and Rift Crystals quests and finds give, and the chance that a drop roll of an enemy or a
gathering spot gives an item. Everything below is read statically from `DDDA.exe` (build 2364871) and
run in a harness on the game's own code, with the game's own tables. **In game: UNKNOWN** until played.

## Where the game keeps them

### The item tables

- `sItemManager` (`[0x018FA4D4]`) loads its tables in one function, `0x0045D730`. It is called once,
  from `0x004FA8C3` with `esi` = `sItemManager` (`8B 35 D4 A4 8F 01 E8 68 2E F6 FF` at `0x004FA8BD`).
  - It starts `8B 8E 10 01 00 00 57 33 FF 3B CF 74 0B` at `0x0045D730`. Each table it holds is released
    through `0x00DE0B90`, which calls slot `0x38` of `sResource` (`[0x018D0AA0]`; `8B 42 38 FF D0` at
    `0x00DE0B9A`). Then ten are asked for by class and name through slot `0x30` of `sResource`.
  - `+0x110` is `"etc\\item\\itemList"` at `0x0155E428` (rItemList, DTI `0x019A85F4`).
  - `+0x120` is `"etc\\item\\itemSetTbl"` at `0x0155E43C`, the gathering spots' sets.
  - `+0x124` is `"etc\\item\\ItemEmListSetTbl"` at `0x0155E450`, the enemies' drops. Both set tables are
    rItemSetTbl (DTI `0x019A8654`).
  - The other seven are the enhancement, mix, curse and random-parameter tables.
- The loader ends by setting five sell prices in the item list
  (`8B 86 10 01 00 00 3B C7 5F 74 67 8B 40 60 05 00 22 00 00 B9 0A 00 00 00 74 03 89 48 4C` at `0x0045D93B`):
  items 68 and 69 sell for 10, item 101 for 50, items 1381 and 1382 for 140.
  `74 03 89 48 4C C3` at `0x0045D9A8` is its last store and its return.

### Items

- rItemList's load stores the record count at `+0x64` (`89 46 64` at `0x00CBFB89`) and copies count x
  0x80 bytes, the file's records as they are, to `+0x60`
  (`8B 4E 64 C1 E1 07 51 50 8D 4C 24 10 89 46 60` at `0x00CBFBB3`).
- A record's buy price is at `+0x48` and its sell price at `+0x4C` (`riftstone.itl`). Bit 25 of the flags
  at `+0x58` is `mGold` (a coin purse) and bit 26 `mRimPoint` (a Rift Crystal item).
- The 1,901 items as shipped:
  - 1,707 have a buy price and 1,621 a sell price.
  - One sells for more than it costs.
  - The four coin purses (1223 to 1226) hold 3,500, 1,000, 100 and 10,000 gold.
  - The ten Rift Crystal items (219, 283, 284, 1743 to 1749) hold 400, 100, 800, 30, 50, 70, 200, 2,000,
    5,000 and 10,000 crystals.
- A shop takes the buy price and pays the sell price: the shop's own gold take is `0x006E0950` and its
  add is `0x006E08D0`. Whether the Rift Crystal shop reads `+0x48`: UNKNOWN.

### Picking up an item: `0x0045F250`

- It takes `ecx` = `sItemManager`, `eax` = who picked it up (4 = nobody in particular) and the item's
  number on the stack. It reads the record and its flags
  (`8B CD C1 E1 07 03 4A 60 56 8B 71 58` at `0x0045F25F`).
- A coin purse (bit 25, `8B F8 C1 EA 19 32 C0 F6 C2 01 74 64` at `0x0045F26E`) gives its sell price
  (`8B 49 4C` at `0x0045F2AB`) through `addGold`. It is doubled when the picker's `+0x26A4` has bit
  `0x10` (`03 C9` at `0x0045F2CB`).
- A Rift Crystal item (bit 26) gives its sell price through `addRiftPoint`
  (`8B D6 C1 EA 1A F6 C2 01 74 12 8B 41 4C 6A 01 50 A1 BC A4 8F 01 E8 18 40 FE FF` at `0x0045F2DE`).

### Drop sets

- rItemSetTbl's load keeps the sets at `+0x60` and their count at `+0x64` (`89 46 60 89 7E 64` at
  `0x00CC061F`).
  - Set i is at `+0x60` + i x 0x2C (`6B C0 2C 03 46 60 5E C2 04 00` at `0x00CC0246`), and the count is read
    as `8B 41 64 C3` at `0x0040E330`.
  - A set in memory is four bytes, then the file's 0x28-byte record: the set's id at `+4`, its rolls at
    `+0x0A`, eight item ids (s16) at `+0x0C` and their eight weights (s16) at `+0x1C`.
- A roll draws r = rand() % 100 + 1. `rand` is `0x00D00690`, stepping the state at `0x018D017C`.
  - Gathering: `33 D2 B9 64 00 00 00 F7 F1 8D 43 1C 42` at `0x00460F04`.
  - Enemies: `33 D2 B9 64 00 00 00 F7 F1 8D 73 1C 42` at `0x00460FFC`.
  - A slot whose weight is 0 or less is skipped. The first slot whose running sum of weights reaches r is
    taken, so a sum past 100 cuts the later slots short.
- A gathering spot (`0x00460EC0`, one roll) takes the first weighted slot reaching r whose id is not 0
  (`0F B7 30 66 85 F6 7E 12 0F BF F6 03 F7 3B D6 7F 07 66 83 78 F0 00 75 13 8B FE` at `0x00460F13`).
  - So a weighted id-0 slot passes its band on to the next weighted slot.
  - Its caller drops a negative id (`66 85 C0 78 27` at `0x0049CEB9`).
- An enemy (`0x00460F60`) rolls as many times as its set says, each roll one result. A negative id gives
  nothing (`0F B7 0E 66 85 C9 7E 0B 0F BF C9 03 CF 3B D1 7E 0D 8B F9 40 83 C6 02 83 F8 08 72 E4 EB 19 0F BF 44 43 0C 85`
  at `0x00461010`).
- The tables as shipped, by the model in `native/plugins/economy/test/run_tests.py`:

| Table | Sets | Never an item | Always an item | An item in 100 rolls, on average | Rolls a set |
|---|---|---|---|---|---|
| gathering (`itemSetTbl`) | 2,800 | 736 | 1,690 | 65.92 | 1 to 5 |
| enemies (`ItemEmListSetTbl`) | 1,116 | 341 | 351 | 47.94 | 0 to 8 |

### Gold and Rift Crystals

- `addGold` (`0x004431E0`, `53 55 8B 6C 24 0C 80 7D 1C 00 56` at `0x004431E0`) takes the amount in `edi`
  and `sGameSys` and a notice flag on the stack.
  - The amount goes through a float (`F3 0F 2A C7 F3 0F 2C D8` at `0x0044320F`).
  - It is added to the Arisen's purse at `sGameSys +0xA76D0 +0x348`, capped at 99,999,999
    (`01 9E 48 03 00 00 81 BE 48 03 00 00 FF E0 F5 05` at `0x00443236`).
  - The gold earned in all is kept at `sGameSys +0xB8858` (`0x0044B2D0`, capped too).
- `addRiftPoint` (`0x00443310`, `53 8B 5C 24 08 56 8B F0 80 7E 1C 00 57` at `0x00443310`) takes `eax` =
  `sGameSys`, and the amount and a notice flag on the stack.
  - The crystals are at `+0x34C`, capped at 99,999,999
    (`01 9F 4C 03 00 00 81 BF 4C 03 00 00 FF E0 F5 05` at `0x00443365`).
  - Their total is at `sGameSys +0xB885C`.
- Both show their notice through the HUD's `0x006547C0`, which ends `C2 04 00` at `0x0065483F`.

The calls the plugin takes:

| Call | What |
|---|---|
| `A1 BC A4 8F 01 8B 7D 08 6A 01 50 E8 6E 33 CB FF` at `0x0078FE62` | a quest's gold, by its kind |
| `8B 15 BC A4 8F 01 6A 01 52 8B F8 E8 1A 2C CB FF` at `0x007905B6` | a quest's gold (`cQuestCtrl::QuestTblResultProc`) |
| `8B 0D BC A4 8F 01 6A 01 51 8B F8 E8 E1 18 CB FF` at `0x007918EF` | a quest's gold |
| `8B BE 24 02 00 00 85 FF 74 2E A1 BC A4 8F 01 6A 01 50 E8 B6 4C BF FF` at `0x0084E513` | the gold a hit gives (`+0x224` of the one hit) |
| `8B 43 14 85 C0 7E 0D 6A 01 50 A1 BC A4 8F 01 E8 13 2D CB FF` at `0x007905E9` | a quest's Rift Crystals (`cQuestCtrl::QuestTblResultProc`) |
| `8B 55 08 A1 BC A4 8F 01 6A 01 52 E8 73 1F CB FF` at `0x0079138D` | a quest's Rift Crystals, by its kind |

Not taken, since what they pay is UNKNOWN:

- `addGold` from `0x004EFBCD`.
- `addRiftPoint` from `0x00501087`, `0x005010B3`, `0x005147A6`, `0x006EB83C` and `0x00738CBF`.

What gives a hit its gold (`+0x224`) is UNKNOWN.

## What the plugin does

- **The tables.** The call at `0x004FA8C3` goes to the plugin, which runs the game's loader and then
  rescales what it loaded, under SEH:
  - **Prices.** Every item's buy price x `[shops] Buy` and sell price x `[shops] Sell`.
    - Where the game's own prices kept the sell price at or below the buy price, the new sell price is
      kept at or below the new buy price, so no item can be bought and sold back for a profit.
    - A coin purse's gold is its sell price x `[gold] Found`.
    - A Rift Crystal item's crystals are its sell price x `[crystals] Found`.
  - **Drops.** In every set, the chance a roll gives an item becomes that chance x `[drops] Enemies` (or
    `Gathering`), at least 1 in 100 and at most every roll.
    - The item slots share the new chance as they shared the old one, by the largest remainder.
    - Each item that could drop keeps a weight of at least 1, while the chance has room for every item.
    - The slots that give nothing shrink to fit. Only weights change, and only of slots the roll reads
      (a weight above 0).
    - A gathering set's id-0 bands are counted as the slot the game gives them to.
    - A set that never gives an item, or always gives one at a multiplier of 1 or more, stays as it is.
  - **Fresh loads only.** The values written are remembered per table. When the game hands the same
    table back, a price or a set already rescaled is not rescaled again; a fresh load, in the same place
    or a new one, is.
- **Rewards.** The six calls above go through the plugin, which changes only the amount and hands
  every register, the flags and `xmm0` to `xmm7` back as they came:
  - a quest's gold x `[gold] Quests`;
  - a hit's gold x `[gold] Found`;
  - a quest's Rift Crystals x `[crystals] Quests`.
- **Whole numbers only.** Rounded to the nearest, at least 1 when the game gave at least 1, and never
  past 10,000,000 (or the game's own value when it is more). So a price stays far below 2^32, and a
  reward stays exact through `addGold`'s float. An amount of 0 or less is the game's.
- **Patching.**
  - A multiplier of 1.0 leaves its part as the game has it; with every one 1.0 nothing is patched.
  - Before patching, the plugin compares 28 sites with build 2364871: the loader's call, start and end,
    the item list's load, the drop sets' load, getter and count, both rolls, the pickup, `addGold`,
    `addRiftPoint` and the six reward calls. On any difference it patches nothing and says why in
    `riftstone\logs\economy.log`.
  - A copy loaded after the tables already exist rescales them at once.

| Key | Range | Shipped |
|---|---|---|
| `[shops] Buy` | 0.1 to 5.0 | 1.0 |
| `[shops] Sell` | 0.1 to 5.0 | 1.25 |
| `[gold] Quests` | 0.1 to 10.0 | 1.5 |
| `[gold] Found` | 0.1 to 10.0 | 1.5 |
| `[crystals] Quests` | 0.1 to 10.0 | 1.5 |
| `[crystals] Found` | 0.1 to 10.0 | 1.5 |
| `[drops] Enemies` | 0.1 to 5.0 | 1.5 |
| `[drops] Gathering` | 0.1 to 5.0 | 1.25 |

What the multipliers do to the shipped drop tables (by the model, the game's own sets only):

| Multiplier | Gathering sets changed | An item in 100 rolls | Enemy sets changed | An item in 100 rolls |
|---|---|---|---|---|
| 1.0 (the game) | 0 | 65.92 | 0 | 47.94 |
| 1.25 | 376 | 67.36 | 425 | 51.80 |
| 1.5 | 376 | 68.53 | 425 | 54.50 |
| 2.0 | 376 | 70.56 | 425 | 58.25 |
| 0.1 | 2,064 | 6.57 | 775 | 4.86 |

Most gathering spots already give an item every time, so `Gathering` above 1.0 changes only the 376
sets that sometimes give nothing.

## Proof (harness)

`native/plugins/economy/test/run_tests.py` maps `DDDA.exe` into a stub process at its fixed base and
loads the built plugin, which verifies and patches that copy. It reads the game's item list and both
set tables from the player's archives (never stored in git) and hands them to the harness. The harness
adds six sets of its own to each table: a negative weight, nothing at all, eight items at 1 each, a
weight past 100, an id-0 band with nothing after it, and a nothing band ahead of the items.

- **The loader.** The game's own loader runs on a fake `sItemManager` whose `sResource` hands it the
  tables. It runs through the patched call and unpatched, and every register (`esp` included) and
  `xmm0` to `xmm7` must come out the same.
  - Every price must be the rule's, applied to the game's prices and the loader's fixes.
  - No byte but the prices may change.
  - No item may sell for more than it costs where it did not before.
- **The rolls.** The game's own two rolls run on every set, before and after, for every r from 1 to 100,
  with the game's generator set to give r - 1.
  - The number of rolls giving an item must be the rule's.
  - Each item keeps its share, within a roll for each slot it holds and each weight of 1 handed out.
  - No item that never dropped may drop.
  - No item that could drop may be lost while the chance has room for every item.
- **Loading again.** The same tables handed back are not rescaled. A fresh load in the same place, and
  one in a new place, are rescaled once.
- **Pickups.** The game's own pickup of each coin purse and Rift Crystal item must pay the new amount.
- **Rewards.** `addGold` and `addRiftPoint` run through each patched call and unpatched with the
  plugin's amount, from the same state, and every byte of `sGameSys` and every register must come out
  the same. The amounts tried are 1, 9, 100, 3,500, 15,000, 250,000, 9,999,999, 30,000,000, 0, -100, and
  a purse and crystals near their cap.
- **The model.** `run_tests.py` applies the rule to the same tables itself and checks the harness's
  digests of every price and every weight, and the pickups' amounts.
- **Profiles.**
  - Rescaling profiles: the shipped ini, shops only (2.0 and 0.5), quest rewards only (3.0 and 0.25),
    the most (5 and 10), the least (0.1 everywhere), and a late load (the plugin after the tables).
  - Profiles that patch nothing: the game's own (all 1.0), `Buy = 6` and `Enemies = lots` (both
    refused), and an exe with one byte of the enemy roll changed (refused).
  - A second copy of the plugin refuses the patched calls.
- **Results.** 868 checks pass.
  - With the shipped ini, 1,553 of the 1,901 items change price, 381 gathering sets and 430 enemy sets
    change (the six added sets counted), and the four coin purses pay 5,250, 1,500, 150 and 15,000 gold.
  - A quest's 15,000 gold becomes 22,500.

In game, what to look for:

- `riftstone\logs\economy.log` notes each time the tables load: how many prices and sets changed, and
  how many were already rescaled. It also says once what the first quest's gold, the first hit's gold
  and the first quest's Rift Crystals became.
- A shop's prices are the new ones, and a coin purse's notice shows its new amount.

Whether every shop, quest and drop the player sees goes through these tables and calls, the Rift
Crystal shop's prices, and how the new economy feels, are UNKNOWN until played.

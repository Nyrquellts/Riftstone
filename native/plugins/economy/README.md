# economy (Riftstone native plugin)

Shop prices, the gold and Rift Crystals quests and finds give, and how often enemies and gathering spots
drop an item. The measurements and the proof are in [`docs/economy.md`](../../../docs/economy.md).

- **Game:** DDDA.exe build 2364871 only.
  - The plugin first compares 28 sites with that build: `sItemManager`'s table loader (its call, start
    and end), the item list's load, the drop sets' load, getter and count, the enemy and gathering rolls,
    the pickup of an item, `addGold`, `addRiftPoint` and the six calls that give a quest's or a hit's
    gold and a quest's Rift Crystals.
  - On any difference it patches nothing and logs why to `<game>\riftstone\logs\economy.log`.
- **Code:** original, no third-party source.
  - It rewrites the loader's call (`0x004FA8C3`): after the game's own load, the plugin rescales the
    item list's prices and the two drop tables' weights in memory. No file of the game changes.
  - It rewrites the six reward calls: each amount passes through the plugin, which changes only the
    amount and hands every register, the flags and `xmm0`-`xmm7` back as they came.
  - Whole numbers only, never past 10,000,000.
- **Settings:** [`economy.ini`](economy.ini), next to the `.asi`:

| Key | Value | What changes |
|---|---|---|
| `[shops] Buy` | 0.1 to 5.0 (shipped 1.0) | What every item costs in a shop. |
| `[shops] Sell` | 0.1 to 5.0 (shipped 1.25) | What a shop pays; never more than buying costs where the game's prices kept it so. |
| `[gold] Quests` | 0.1 to 10.0 (shipped 1.5) | A quest's gold. |
| `[gold] Found` | 0.1 to 10.0 (shipped 1.5) | A coin purse's gold, and the gold a hit gives. |
| `[crystals] Quests` | 0.1 to 10.0 (shipped 1.5) | A quest's Rift Crystals. |
| `[crystals] Found` | 0.1 to 10.0 (shipped 1.5) | A Rift Crystal item's crystals. |
| `[drops] Enemies` | 0.1 to 5.0 (shipped 1.5) | The chance each roll of an enemy's drop gives an item (at most every roll). |
| `[drops] Gathering` | 0.1 to 5.0 (shipped 1.25) | The same for a gathering spot. |

The items keep their shares of a drop's chance, and an item that could drop keeps at least 1 roll in 100
while the chance has room for every item. A multiplier of 1.0 leaves its part as the game has it; all 1.0
patches nothing.

`economy.log` notes each time the tables load (how many prices and sets changed), and once what the first
quest's gold, the first hit's gold and the first quest's Rift Crystals became.

In game: UNKNOWN until played.

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\economy\build.cmd
```

| File | What |
|---|---|
| `out\economy.asi` + `out\economy.ini` | the plugin and its settings |
| `out\economy_stub.exe` + `out\economy_harness_core.dll` | the test harness |

## Test (no game launched)

```bat
python native\plugins\economy\test\run_tests.py
```

It maps `DDDA.exe` read-only into the stub, loads the plugin, and runs the game's own table loader on a
fake `sItemManager` with the item list and drop tables read from your archives, the game's own enemy and
gathering rolls on every set for every roll from 1 to 100, its own pickup of each coin purse and Rift
Crystal item, and its own `addGold` and `addRiftPoint` through each patched call and unpatched; the
digests are checked against the same rule applied in Python. Ten profiles, 868 checks.

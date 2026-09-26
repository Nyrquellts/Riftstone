# enemy_cap

Riftstone's own loader plugin: more enemies active at once. Dragon's Dogma: Dark Arisen keeps at most
10 enemies active (the rest wait, nearest first, until a slot frees); with this plugin the limit is 30
by default, or anything from 10 to 64 in `enemy_cap.ini`:

```ini
[enemy_cap]
slots = 30
record = 1
```

How it works, and why the save file, post-Dragon Gran Soren's limit of 5 and the distance-priority
weight stay as they are: `docs/re-enemy-cap.md`. DDDA.exe build 2364871 only: all 164 patched
instructions and the four replaced runs are compared byte for byte first, and the game's spawn manager
must not exist yet (it is built at the patched size). If either fails, nothing is patched and
`riftstone\logs\enemy_cap.log` says why.

**The slot record** (`record = 1`, the default): `enemy_cap.log` also keeps how full the pool was:
each new peak, when every usable slot was taken, a status line a minute, and the last reading when the
game exits normally, each with the time and the stage. If a session ends badly, send that file along
with the loader's report. It only reads; `record = 0` turns it off.

```bat
native\plugins\enemy_cap\build.cmd
riftstone loader plugin add native\plugins\enemy_cap\out\enemy_cap.asi
```

`riftstone loader plugin add` also copies `enemy_cap.ini` the first time; edit the copy in
`<game>\riftstone\plugins\`. Remove with `riftstone loader plugin remove enemy_cap.asi`.

**In game (2026-09-25):** it works at 30: about 25 goblins fought at once, then about 20 more nearby.
One session ended while the owner gathered both groups. It left no crash or fatal-error report, and
the game went through its normal shutdown; the cause is UNKNOWN (`docs/re-enemy-cap.md`, "In game").
More enemies cost CPU for their AI and animation, and memory.

**Proof:** `test/run_tests.py` (part of `tools\test_all.cmd`) maps a read-only copy of DDDA.exe, lets the
plugin patch it, and runs the patched game code on a fake spawn manager with 30, 12, 64 and 5 (clamped
to 10) slots: the constructor, clear, reset and final runs, the destructor's loop, three real functions
that reach the last slot, and the slot record's reader on a fake manager in stage 100. Its log must
hold the peaks, the full pool and the exit line written at process exit. Three more runs: `record = 0`
(no record), an altered instruction, and a spawn manager that already exists (both refused).
`tests/test_enemy_cap.py` checks the table against the exe.

Original code; no third-party source.

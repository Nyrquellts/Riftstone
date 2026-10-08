# Collision entry nodes: the 800-a-frame table, its overshoot, and the `collision_cap` plugin

What crashed the owner's game in Gran Soren five times (2026-09-28 12:47 and 13:40, 2026-10-06 15:02, 15:03 and
16:09; `riftstone\logs\crash-*.txt`), and how the plugin lifts the limit. DDDA.exe build 2364871; every byte
quoted here is checked by `tools/doc_claims.py check`. The class layout comes from the PS3 build's symbols
(`sObjCollision`, `cObjCollision::EntryNode`), with the PC offsets measured in the code below.

## The manager

`sObjCollision` (instance pointer at `0x018FA4E4`) is 0x9C550 bytes: its DTI registration pushes the size
(`68 50 C5 09 00` at `0x0132C466`), as do the game's own construction of it (`68 50 C5 09 00` at `0x0041C57F`)
and the DTI's `newInstance` (`68 50 C5 09 00` at `0x004785DB`). Inside it:

| Offset | Field | What |
|---|---|---|
| `+0x34` | `mEntryNodeCtr` | how many entry nodes this frame asked for |
| `+0x38` | `mEntryNodeCtrMax` | the peak, kept across frames |
| `+0x40` | `mEntryNode[800]` | the table: 800 records of 0x320 bytes (`cObjCollision::EntryNode`) |
| `+0x9C440` .. | the tail | `mNodeHitInfoCtr`, its peak, eight lists of 0x14, the sweep's cursor (`+0x9C4F4`), an array (`+0x9C514`) |

The constructor (`0x00478600`) builds the 800 records in a loop counted from 799 down (`C7 44 24 10 1F 03 00 00`
at `0x0047862B`; the first record at `+0x40`: `8D 6E 40` at `0x00478615`), each through the node initialiser
`0x007735C0`; the destructor tears them down the same way (`BB 1F 03 00 00` at `0x00478A05`).

## The allocator overshoots

`sObjCollision::getEntryNode` (`0x007708B0`) hands out the next record. It loads the manager
(`A1 E4 A4 8F 01` at `0x007708B0`), takes the count's address (`83 C0 34` at `0x007708B8`), increments it with
`InterlockedIncrement` through the import slot (`FF 15 D0 D0 39 01` at `0x007708BC`) and only then compares the
result with 800 (`3D 20 03 00 00` at `0x007708C2`): past it the caller gets NULL, else the record's address
(`69 C0 20 03 00 00` at `0x007708CD`). The same sequence is inlined at 96 more places (97 bounds in all, each
`cmp eax, 800` right after the increment; `tools/collision_cap_sites.py` finds them). So a frame that asks for
more than 800 nodes is refused the extras, but the count keeps rising: a frame of 823 requests leaves the count
at 823. The frame's end puts it back to 0 (`89 58 34` at `0x0041DF8A`) after keeping the peak
(`8B 48 34 39 48 38 7D 03 89 48 38` at `0x0041DF7E`).

## The sweep trusts the count

The job `0x00479C10` (queued by `0x00479CB0`, which zeroes the cursor at `+0x9C4F4`) sweeps the entry nodes in
parallel: each step takes the next index through `InterlockedIncrement` on the cursor (`8D 9E F4 C4 09 00` at
`0x00479C1B`), stops when it reaches the count (`3B 46 34 7D 7A` at `0x00479C24` before the loop,
`3B 46 34 7C 8E` at `0x00479C9D` after it), makes the record's address (`69 C0 20 03 00 00` at `0x00479C30`,
`8D 4C 30 40` at `0x00479C36`) and, for three pointers the record holds (`8B 81 4C 01 00 00` at `0x00479C3A`,
then `+0x154` and `+0x158`), reads the object's kind (`8B 40 04` at `0x00479C44`, `83 E0 07` at `0x00479C47`)
and keeps the pointer when the kind is 1 or 2, else drops it. The cursor starts at 1, so record 0 is never
swept.

Nothing clamps the index at 800. With the count at 823 the job sweeps "records" 801..823, which lie over the
tail fields and past the end of the manager: it reads a pointer there and faults on `[pointer+4]`. All five
reports fault at `0x00479C44` (one at `0x00479C64`, the `+0x154` pointer's read), each with the record past the
800th by its registers (record 823 three times, 801 once, 824 at 16:09 with the plugin not yet installed: index = (ECX - ESI - 0x40) / 0x320), reading addresses
3 and 5. Each time the game had just opened `em5301.arc` (the Archydra) while loading stage 220, where the mod
`Gran Soren Arena` places six Archydras in group 0: a city of people and objects plus six many-shaped monsters
asks for more than 800 nodes in one frame.

The `stage_enemies` plugin was blamed at first (`docs/stage-enemies.md`); it was armed for stage 220 at the two
2026-09-28 crashes and off at the two of 2026-10-06. The cause is the table.

## What `collision_cap` does

`native/plugins/collision_cap` (`entry_nodes` 800..16384, default 4096; `tools/collision_cap_sites.py` writes
`src/sites.inc`, `--check` compares it):

| Sites | Kind | Change |
|---|---|---|
| 169 | `K_TAIL` | a displacement of a field after the table (`+0x9C440`..`+0x9C54F`) moved by `(N - 800) x 0x320` |
| 4 | `K_TAIL_IMM` | `add reg, imm` forming such a field's address, moved the same way |
| 97 | `K_BOUND` | `cmp eax, 800` after the count's increment: 800 becomes N |
| 2 | `K_COUNT_M1` | the constructor's and destructor's loop counts: 799 becomes N - 1 |
| 3 | `K_ALLOC` | `push 0x9C550`: the manager's size grows by the table's growth |
| 2 blocks | | the job's two count checks (`0x00479C24`, `0x00479C9D`) become jumps to the plugin, which compares the index with the count **and with N** |

The table stays where it is (so the 97 allocators' `+0x40` stays) and everything after it moves. The 13 other
`cmp eax, 800` in the exe compare the stage number (stage 800, the Everfall's entrance) and are left alone; the
generator tells them apart by the increment before each allocator's compare. The tail displacements are read
only in functions that hold the manager's global, are its own methods (`0x00478600`..`0x0047A000`) or are
listed as read by hand (sMain's frame end `0x0041DD60`, which reaches it through sMain's own table); five of
those functions also hold `sGameSys`'s global (`0x018FA4BC`, a bigger class whose fields reach the same
offsets), and each of their tail sites was read by hand: every base register was loaded from `0x018FA4E4`.

At 800 the plugin changes nothing but the two checks: the sweep then stops at the table's end even when the
count passed it, which alone removes the crash. The nodes a frame can use are then still 800, so a crowded
frame loses hit shapes; 4096 keeps them.

Before patching, the plugin verifies all 275 instructions and both blocks byte for byte and refuses when the
manager already exists (`[0x018FA4E4]` not 0: it was built at the old size). The harness (`test/run_tests.py`)
maps the real exe into a stand-in process and runs, with the patches in: the constructor's record loop (all N
records built as the game builds its 800, nothing written past them), the allocator asked N + 20 times (records
1..N-1 handed out, the rest refused, the count at N + 20), the sweep job with the count at N + 50 over records
laid out past the table (1..N-1 swept, record 0 and the records past the table untouched, the cursor at N; then
with the count at 5 and at 0), the destructor's loop and the reset; at 800, 1024, 4096, 16384 and the two
clamped settings, plus one altered instruction and one late load, both refused.

In game: UNKNOWN until played. What to look for: Gran Soren loads with `Gran Soren Arena` installed and no
`crash-*.txt` appears; `collision_cap.log` names the size it patched.

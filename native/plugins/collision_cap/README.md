# collision_cap

Riftstone's own loader plugin: room for more hit shapes in one frame, and the crash a crowded frame causes is
stopped. Dragon's Dogma: Dark Arisen keeps a table of 800 "collision entry nodes" for the frame: every hit
shape the frame's objects enter (a body part, a weapon, a shot, a wall check) takes one. A frame that asks for
more than 800 crashes the game, not because the request is refused (it is) but because the count of requests
keeps rising past the table and the job that sweeps the nodes trusts that count: it reads records past the
800th, past the manager, and faults on what it finds there (Riftstone's crash report at `0x00479C44`, seen four
times in Gran Soren with six Archydras placed by a mod). With this plugin the table holds 4096 by default, or
anything from 800 to 16384 in `collision_cap.ini`, and the sweep stops at the table's end whatever the count says:

```ini
[collision_cap]
entry_nodes = 4096
```

The table sits inside the manager, so the plugin grows the manager in place: every field after the table moves
by the table's growth (173 instructions), each of the 97 places that allocate a node compares against the new
size, the constructor's and destructor's loops build and tear down the new count, the three allocations of the
manager ask for the new size, and the sweep job's two count checks jump to the plugin, which clamps them. Each
node is 800 bytes, held once for the session: 4096 nodes are 3.2 MB. `docs/re-collision-cap.md` has all of it.

DDDA.exe build 2364871 only. Before patching, the plugin compares byte for byte all 275 patched instructions and
the two replaced checks, and the game's collision manager must not exist yet (it is built at the enlarged
size). If either check fails, nothing is patched and `riftstone\logs\collision_cap.log` says why.

```bat
native\plugins\collision_cap\build.cmd
riftstone loader plugin add native\plugins\collision_cap\out\collision_cap.asi
```

`riftstone loader plugin add` also copies `collision_cap.ini` the first time; edit the copy in
`<game>\riftstone\plugins\`. Remove with `riftstone loader plugin remove collision_cap.asi`.

`test\run_tests.py` maps the real DDDA.exe into a stand-in process (no game launched), loads the plugin and runs
the patched game code on a fake manager: the constructor's record loop, the allocator asked for more than the
table holds, the sweep job with the count past the table, the destructor's loop and the reset, at six sizes, plus
one altered instruction and one late load, which the plugin must refuse. In game: UNKNOWN until played.

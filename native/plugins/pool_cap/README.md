# pool_cap

Riftstone's own loader plugin: bigger memory pools for the game's enemies, physics, collision, effects and
arrays, and a log of every request a pool refused. Dragon's Dogma: Dark Arisen takes eight memory pools once, at
start-up, each a fixed size the exe pushes (no file configures them), and never grows them: a request a full pool
cannot place is refused. The "Unit" pool, 64 MiB, holds every enemy, player, model, rigid body, ragdoll and AI
object (2,925 classes). A ragdoll takes the four arrays of its bodies from it, and when one is refused the game
leaves the ragdoll with no body data: the state the loader's ragdoll guard answered about 19,000 times in 7 s in
the owner's session of 2026-10-06 (62 enemies at once). Whether the pool was full then is UNKNOWN; this plugin's
log says it.

With this plugin the pools are, by default, four times the game's for the five whose contents grow with the
enemies, and the game's own for the other three:

```ini
[pool_cap]
unit = 256
physics = 48
collision = 96
effect = 20
array_string = 24
gui = 5
temp = 64
system = 64
```

Each value lies between the game's size and a most (Unit 64..1024 MiB; `pool_cap.ini` lists each), all eight
together at most 1280 MiB of the game's 4 GB of address space. A pool the PC cannot give its size (VirtualAlloc
refused) is made at the game's size instead.

The plugin changes the eight size pushes of the pool builder (`0x00740590`) and wraps three entries of the
pools' vtable (`0x0155AD4C`): the init, so a refused size falls back, and the two allocation entries every request
of every pool goes through, to count what each pool refused, the largest refusal and how deep into the pool its
requests reach. `riftstone\logs\pool_cap.log` gets the first refusals of each pool with the game code that asked
(a refusal from the ragdoll setup `0x01083260` is named and counted as one ragdoll left with no body data), a line
each time a pool's count doubles or its depth passes a quarter, and a summary at exit. `docs/re-memory-pools.md`
has all of it.

DDDA.exe build 2364871 only. Before patching, the plugin compares byte for byte the eight pushes with the pool
names and types beside them and the three vtable entries with the code they point to, and the pools must not
exist yet (the loader loads plugins before the game's start-up code runs). If either check fails, nothing is
patched and the log says why.

```bat
native\plugins\pool_cap\build.cmd
riftstone loader plugin add native\plugins\pool_cap\out\pool_cap.asi
```

`riftstone loader plugin add` also copies `pool_cap.ini` the first time; edit the copy in
`<game>\riftstone\plugins\`. Remove with `riftstone loader plugin remove pool_cap.asi`.

`test\run_tests.py` maps the real DDDA.exe into a stand-in process (no game launched), loads the plugin and runs
the game's own pool builder, allocator and ragdoll setup: the eight pools built at the ini's sizes (each one
commit of its size), the Unit pool filled until it refuses (the requests it gives reach its end), the setup
refused at its first array, at its third in a 1 MiB hole and, as a goblin's ragdoll (19 bodies, 80-byte arrays),
once the small heap is full: each time it frees what it got, clears the body data and gives the resource back. The
cases: the shipped ini, the game's sizes, the most, values out of range, too much in all, no memory for the larger
size, one altered instruction and a late load (both refused). In game: UNKNOWN until played.

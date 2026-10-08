# The game's memory pools, the Unit pool's limit, and the `pool_cap` plugin

Dark Arisen (DDDA.exe build 2364871, read 2026-10-07) allocates every engine object from one of eight memory
pools that WinMain builds once, each a fixed size the exe pushes as an immediate. Nothing configures them, and none
grows: a request a full pool cannot place is refused (NULL). The "Unit" pool, 64 MiB, holds every enemy, player,
model, rigid body, ragdoll and AI object. The ragdoll setup takes its body arrays from it, and a refusal leaves a
ragdoll with no body data: the state behind the crashes `docs/stability-membrane.md` ("Why a ragdoll has no body
data") traces. The `pool_cap` plugin (`native/plugins/pool_cap`) makes the pools larger before they exist and
counts every refusal. `tools/pool_census.py` re-reads the pools, the class map and every class's pool from the
exe; the harness (`native/plugins/pool_cap/test/run_tests.py`) runs the game's own builder, allocator and ragdoll
setup. In game: UNKNOWN until played.

## The allocator table

An object's allocator is entry `(DTI+0x18 >> 23) & 0x3F` of a table of 64 at `0x01876628`: getAllocator is
`8B 44 24 04 8B 48 18 C1 E9 17 83 E1 3F 8B 04 8D 28 66 87 01 C3` at `0x00CF5E20` (cdecl, the DTI as its argument).
A CRT initialiser (`0x0137E090`) points all 64 at the static MtDefaultAllocator at `0x01876840`
(`B8 40 68 87 01 B9 40 00 00 00 BF 28 66 87 01` at `0x0137E09B`, then `F3 AB` at `0x0137E0AF`). WinMain then builds
the pools (`E8 04 8B 33 00` at `0x00407A87`, to `0x00740590`) and maps class names to slots (`E8 CF 8D 33 00` at
`0x00407A8C`, to `0x00740860`). No file is read for either: the sizes are push immediates and the map is code.

## The eight pools

`0x00740590` builds eight MtScalableAllocator objects (0x4D0 bytes each through operator new `0x00D0F2B0`, the
constructor `0x0041B050` stores the vtable at `0x0155AD4C`, `C7 03 4C AD 55 01` at `0x0041B06B`) and initialises
each through vtable +0x30 (`8B 52 30` at `0x00740661`) with six pushes, the name (with its quotes, `"Unit"`), a type
and the size among them. Each is then stored in its slot (the Unit pool: `89 35 58 66 87 01` at `0x00740686`).

| Pool | Size | Size pushed | Slots |
|---|---|---|---|
| Temp | 64 MiB | `68 00 00 00 04` at `0x007405CB` | 5 |
| System | 64 MiB | `68 00 00 00 04` at `0x0074061A` | 11, 16 |
| Unit | 64 MiB | `68 00 00 00 04` at `0x00740669` | 12, 17, 25 |
| Effect | 5 MiB | `68 00 00 50 00` at `0x007406B8` | 18 |
| GUI | 5 MiB | `68 00 00 50 00` at `0x00740707` | 19 |
| Array/String | 6 MiB | `68 00 00 60 00` at `0x00740756` | 2, 3, 13 |
| Collision | 24 MiB | `68 00 00 80 01` at `0x007407A5` | 4 |
| Physics | 12 MiB | `68 00 00 C0 00` at `0x007407F4` | 15 |

The tail of `0x00740590` copies slots into others: the Unit pool into 17 (`89 0D 6C 66 87 01` at `0x0074081B`) and
25 (`89 0D 8C 66 87 01` at `0x00740821`), System into 16, Array/String into 2 and 13, and the still-default slot 10
into 1, 14 and 23. Later, sBbsRpgMain's constructor makes a ninth MtScalableAllocator, "AIWork", handed 1 MiB
(`68 00 00 10 00` at `0x0041D022`, the name `68 28 A3 55 01` at `0x0041D028`, initialised through vtable +0x2C,
`FF D2` at `0x0041D02F`), and puts it in slot 25 (`89 35 8C 66 87 01` at `0x0041D031`). The constructor has ten
callers: the eight pools, AIWork (`0x0041D009`) and the class's own `newInstance` (`0x00D0F980`). Together the
eight take 244 MiB of the game's 4 GB of address space.

## Which class uses which pool

`0x00740860` adds 439 `{name, slot}` entries to a list (`push slot; push name; call 0x00CF5D60`; the Unit entry is
cUnit's, `6A 0C 68 44 B8 58 01` at `0x007409C1`) and applies it once (`E8 36 3E 5B 00` at `0x007423E5`). The lookup
returns the first entry of a name (`0x00CF5DC0`, `8B 46 08` at `0x00CF5E15`). The walk (`0x00CF6220`, then
`0x00CF60B0` per class) takes a class's own name, else each parent's below the root, else the name with its last
`::` scope stripped (`0x00CF5EA0`), and copies the slot it finds to every class below (`0x00CF5750`) before
visiting them. A DTI's constructor (`0x00CF5780`, MtDTI's vtable `C7 06 10 35 40 01` at `0x00CF57AC`) sets the six
bits to the index it is given (`C1 E0 17` at `0x00CF57DC`, `25 00 00 80 1F` at `0x00CF57E2`, `31 46 18` at
`0x00CF57E7`), and every DTI is constructed before WinMain, so the walk, run from WinMain, has the last word. The DTI
of uRigidBody registers index 0 (`docs/stability-membrane.md` quotes the constructor call at `0x01384EC0`), and the
walk gives it 12 through its ancestors uPhysics and cUnit (the map names cUnit, not the other two). No other code
found sets those bits: the only `and`/`or` with an immediate on bits 23..28 of a word at +0x18 are four `or`s of
bits 25 and 26 (`81 4F 18 00 00 00 02` at `0x00742DA8`, `81 4E 18 00 00 00 02` at `0x007983BD`), on objects that
keep a float at +0x50 (`F3 0F 11 47 50` at `0x00742DC1`) and are not DTIs.

`tools/pool_census.py` repeats the walk on all 4,405 DTI constructions in `.text` (none left unread):

| Pool | Classes | Of them |
|---|---|---|
| Unit (slots 12, 17) | 2,925 | the 96 enemy classes `uEm####` (29,280 to 32,976 bytes, median 29,824), uHumanEnemy 29,696, uEnemy 24,576, uPlayer 23,056, uCharacterBase 12,176, uModel 3,664, uRagdoll and uRagdollExt 432, uRigidBodyExt 384, uRigidBody 368, uRagdoll::RIGID_BODY_INFO 288, uCnsRagdoll 224, cEm0100ActRagdoll 120, cRigidBody 68; slot 17 holds the 497 AI classes the map gives it through nAI and cAIObject (cAIActionFSM and the rest) |
| AIWork (slot 25) | 10 | path finding: cAIAStar and the path traces |
| Physics | 13 | nPhysics::System 152,208, nPhysics::RigidBody 168 |
| System | 289 | |
| GUI | 197 | |
| Array/String | 93 | |
| Collision | 53 | |
| Effect | 31 | |
| Temp | 6 | |
| MtDefaultAllocator | 788 | slot 10, cResource's: 736 classes, rRagdoll and rRigidBody among them |

Besides the classes, 24 calls take the Unit slot themselves (`mov reg, [0x01876658]`, then vtable +0x20 with a
size computed at run time) and 41 free into it; which data they hold is UNKNOWN.

## How a pool places a request

The pool's init (`0x00D0FA50`, vtable slot 12) stores its size (`89 7E 68` at `0x00D0FAD3`), commits all of it at
once (`E8 23 5A FE FF` at `0x00D0FB08` to `0x00CF5530`, VirtualAlloc with MEM_COMMIT: `68 00 10 00 00` at
`0x00CF5543`, `FF 15 B4 D1 39 01` at `0x00CF554B`) and makes the region one free block (`0x00D0F660`), whose size is
a 27-bit count of 16-byte units (`81 E3 FE FF FF 0F` at `0x00D0F6C1`): a pool can hold up to 2 GiB. Requests go
through vtable slots 6 and 13: slot 8 (+0x20, alloc(size, align), the one the game calls) passes on to slot 6
(`8B 40 18` at `0x00CF53E6`, then `FF D0` at `0x00CF53F3`); slot 9 (+0x24) frees. A request over 64 KiB
goes to the last of the pool's heaps (`81 FE 00 00 01 00` at `0x00D100FC`), a smaller one to one of the small
heaps; a heap takes chunks from the region rounded to the pool's grain (64 KiB for Unit), with a 0x30-byte header
per block. So a 1 MiB request costs 1,114,112 bytes, and a full region refuses even a small request once its small
heap has no free block left.

Measured in the harness (SIMULATED, the game's own code): the Unit pool at 64 MiB gives 59 requests of 1 MiB
(reaching 63.5 MiB), at 256 MiB 240 (255.8 MiB), at 1024 MiB 962 (1,022.9 MiB). With the region used up, the small
heap that serves 80 bytes gives 122 to 531 more such requests, then refuses.

## The ragdoll's arrays and an enemy's share

The setup (`0x01083260`) stores the ragdoll's resource as its body data (`89 7E 38` at `0x010832A9`), asks
getAllocator for uRigidBody's allocator (`68 78 39 8D 01 E8 12 2B C7 FF` at `0x01083304`) and takes four arrays
through vtable +0x20 (`8B 50 20` at `0x0108332E`): the body count times 4, 4, 16 and 16 bytes, each rounded up to
16. The results go to +0x4C, +0x50, +0x54 and +0x58 (`89 46 4C` at `0x0108333B`, `89 46 50` at `0x01083394`,
`89 46 54` at `0x010833E1`, `89 46 58` at `0x01083438`). When the first is refused the setup gives the resource back
and clears the body data (`C7 46 38 00 00 00 00` at `0x01083354`); a later refusal frees the arrays it got first
(`89 7E 38` at `0x0108347D`). The setup answers false either way.

The body count is the rRigidBody header's: `0x01116F40` reads a 0x30-byte header `RBD\0` (`81 3F 52 42 44 00` at
`0x01116F59`) and the count is its +0x68 shifted right by 8 (`8B 46 68` at `0x01116F75`, `C1 E8 08` at
`0x01116F7B`); in a ragdoll resource (`.rdd`, `RDD\0` checked by `81 3F 52 44 44 00` at `0x01054A69`) it follows the
ragdoll's own data. The game's 26 ragdoll resources have 4 to 23 bodies. The Goblins'
(`e0100`) has 19, so its setup asks for 80, 80, 304 and 304 bytes, 768 in all.

An enemy's share of the Unit pool, as far as the exe and the data say it: its object (29 to 33 KB), its ragdoll's
arrays (under 1 KB) and the parts the classes above add (a uModel of 3,664 bytes, rigid bodies of 368, AI objects),
each with a 0x30-byte header. That is tens of kilobytes, against the 1 MiB an enemy would have to take for 62 to
fill 64 MiB alone. What else an enemy takes from the pool at run time (the 24 direct calls, motion and model work,
other units on the stage) is UNKNOWN until measured, and so is whether the Unit pool was full in the owner's session
of 2026-10-06. A refusal of a ragdoll's 80 bytes means its small heap and the region had nothing left; a resource
with no bodies, or a walk after a teardown, would leave the same null body data without any refusal.

## What `pool_cap` does

`native/plugins/pool_cap` (`pool_cap.ini`, `[pool_cap]`, sizes in MiB): it writes the eight size pushes of
`0x00740590` (default four times the game's for Unit 256, Physics 48, Collision 96, Effect 20 and Array/String 24;
GUI, Temp and System keep the game's; each between the game's size and a most, Unit at most 1024; all eight at most
1280). Each push is compared byte for byte with the name and type pushed beside it first. It wraps three entries of
the vtable `0x0155AD4C`: the init (slot 12), so a pool the PC cannot give its larger size (VirtualAlloc refused,
probed through the exe's own import) is made at the game's size instead of empty, and the two allocation entries
(slots 6 and 13), to count what each pool refused, the largest refusal and how deep its requests reach. The plugin
loads before the game's start-up code runs (the loader loads plugins from its DllMain); it refuses when the pools
already exist (the Unit slot is neither empty nor the default allocator) or any byte differs, and then patches
nothing. AIWork and other allocators handed their memory are counted apart and not resized.

`riftstone\logs\pool_cap.log` names each refusal's pool, size and the game code that asked (return addresses on
the stack, MtAllocator's pass-on entries left out). A refusal whose request came from the setup
(`0x01083260..0x0108350A`) is named as "the ragdoll setup, so a ragdoll has no body data" and counted apart. A line
follows each time a pool's count doubles or its depth passes a quarter, and a summary at exit (each pool's size,
depth and refusals; the ragdoll setup's refusals; other allocators').

The harness (SIMULATED): the real `0x00740590` builds the eight pools at the sizes of nine cases (the shipped ini,
the game's, the most, out of range, too much in all, no memory for 256 MiB, an altered push, a late load). Each pool
is one MEM_COMMIT of its size with its quoted name, and the aliases and AIWork-style handed allocators behave as in
the game. The Unit pool is filled until it refuses. The real ragdoll setup is then refused at its first array
(300,000 bodies), at its third in a 1 MiB hole (48,000 bodies) and, as a Goblin's ragdoll (19 bodies), at its first
80-byte array once the small heap is full. Each time it answers false, clears the body data, keeps no array and
gives the resource back; the hole is whole again afterwards; the log names the setup's call (`0x0108333B`,
`0x010833E1`) and counts three.

In game: UNKNOWN until the owner plays. What to look for: `pool_cap.log` starts with the sizes it patched; after a
fight with many enemies, its summary says how deep the Unit pool's requests reached and whether the ragdoll setup
was refused. If it was, the pool ran dry, and the size in the ini is the lever. If it was not, while the loader's
`ragdoll_bodies` guard still answered walks, the null body data came the other way (no bodies, or a walk after a
teardown).

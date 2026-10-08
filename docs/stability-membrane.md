# The stability membrane: guards against the crashes Dark Arisen's engine lets through

MT Framework trusts its data completely. A resource that is missing, late, half set up or in another game's
layout walks straight into memory it does not own, and the game ends on `0xC0000005`. The owner's brief of
2026-09-27 asked for the opposite: a game modders can load imperfect assets into without it dying. This
page is the plan and its state. Every guard is byte-verified against build 2364871, off with one `[guard]`
key in `riftstone_loader.ini`, counted, and written to `riftstone\logs\riftstone_error.log`. What happens
in game stays UNKNOWN until played.

## The crashes on record (evidence, not guesses)

| Where | Seen | What the engine did | Guard |
|---|---|---|---|
| `0x00794942`, a ragdoll's body walk | the owner's session of 2026-09-27 20:53 (a goblin in a horde of about 50) | read the body count through a container whose body data (`+0x38`) was not set up yet | `ragdoll_bodies` (built) |
| `0x00794AA2`, a dying enemy's ragdoll walk | the owner's session of 2026-10-06 22:33 at Devil's Firegrove (stage 100; a goblin `uEm0101` in its death action `cEmActActingDie`, 21 minutes in) | the same read in a third sibling of the two hand-guarded walks: `8B 48 38 56 33 F6 F7 41 68 00 FF FF FF` at `0x00794A9C` loads `bodydata = [container+0x38]` and tests the count with no null-check; the death action calls it (`E8 F1 E1 EE FF` at `0x008A689A`) with the enemy's ragdoll owner at `+0x1ED8`. The first scan missed it | `ragdoll_bodies` family (completed 2026-10-06) |
| `0x007945B4`, and 54 more copies of the same idiom | the owner's session of 2026-09-27 (another goblin horde at Gran Soren) | the same read one function over: `8B 46 38` at `0x007945AB` loads `bodydata = [holder+0x38]`, then `F7 40 68 00 FF FF FF` at `0x007945B4` reads `[bodydata+0x68]` with no null-check | `ragdoll_bodies` family (built) |
| `0x010CBFA4` in Dark Arisen, an effect-system tag dispatch | three times on 2026-09-26 (converted Online effects, the compat layer) | read the tag byte `0F B6 40 03` at `0x010CBFA4` through `a1`, a parameter block in the source game's layout, so `a1` points nowhere (`0x8227CFE0`) and is not null | `particles` (built) |
| `0x00606628`, a GUI text field | the owner's session of 2026-09-27 22:56 (the world map, with 13 Portcrystals placed, one named) | `8A 11` at `0x00606628` walks a null string: `0x006065E0` looks a string up (`0x00607960`, null when it is not in sGameSys's table) and its own null-check tests the wrong pointer -- `8D 8F D0 76 0A 00` at `0x00606619` tests sGameSys+0xA76D0, an address that is never null | `gui_text` (built) |
| `0x00479C44`, the collision sweep job | the owner's sessions of 2026-09-28 (stage 220, two at start-up) and 2026-10-06 (Gran Soren with six Archydras, five) | read a pointer of an entry record past the table's 800th: `getEntryNode` raises the count before it compares it with 800, so a crowded frame leaves the count past the table (`docs/re-collision-cap.md`) | `collision_cap` plugin (built 2026-10-06) |
| `MtFile::open` (`0x00D0D0C0`) and its "Failed open file" box | players' most reported crash (the ending's `credit2_01_99.gmd`) | a resource asked for as a loose file before its archive was read, or a texture that does not exist, ends in `exit(1)` | `missing_textures`, `from_archives` (1.0.1) |

The top three beyond ragdolls are therefore the effect system's record reads, the loose-file fatal path, and
(the third, not a code site) running out of a 32-bit process's address space, which the loader already
watches (`[memory]`) and which DXVK eases (`docs/runtime.md`). No crash on record comes from animation tracks,
sound cues or the enemy tables; their guards below say what the code shows.

## How a guard works

- **A check where the engine forgot one.** The game's own code usually has the right answer next door: its
  body-count accessor (`0x010805D0`) returns 0 without data, while four inlined copies did not check. Such a
  guard puts the game's own rule where it was left out, and changes nothing for data that is sound.
- **Containment for cosmetic work.** A particle generator's update runs inside a structured exception frame
  of the loader's. A fault in the game's own code there ends that generator's update, switches the generator
  off the way the game's loop reads it (the list walk at `0x00E6D070` skips a generator whose `+0xC` bit 0 is
  clear), counts it and logs it. Only effect work is contained: skipping half-run AI, physics or save code
  would leave the game running on broken state, which is worse than a crash report.
- **Assets checked at the door.** A loose file that is not what its type needs (a truncated texture) is
  answered as if it were missing: the archive's own copy when the game has one, else the stand-in.

A process-wide vectored exception handler that "skips the offending instruction" was considered and not
built: it sees every exception of every module (the driver's, DXVK's, C++ throws), and skipping an unknown
read lets the game go on with a register that holds nothing meaningful. Containment gives the same "the game
goes on" at the level of one effect generator, with the compiler unwinding a frame it knows.

## The guards

| `[guard]` key | What it stops | State |
|---|---|---|
| `missing_textures` | a texture that is not there: the stand-in instead of "Failed open file" | built (1.0) |
| `from_archives` | a resource asked for loose before its archive was read: its own bytes | built (1.0.1) |
| `ragdoll_bodies` | a ragdoll walked before its bodies are set up: four bespoke sites, then the whole family `tools/ragdoll_sites.py` finds (62 more) | built (1.0.2; the family completed 2026-10-06) |
| `particles` | a fault reading an effect's parameter block (a converted Online effect): the dispatch returns the game's default | built (1.0.2) |
| `shadow_buffers` | a raised sun shadow map past what the GPU can create (lamp maps are half of it, so they follow): bounded to the device's largest texture | built (1.0.2) |
| `broken_textures` | a loose `.tex` whose header or mip data does not fit its file: answered as missing (a good copy or the stand-in) | built (1.0.2) |
| `gui_text` | a GUI text field whose looked-up string is missing (a Portcrystal or place the game has no label for): empty, not a null walk | built (1.0.2) |
| `animation_tracks` | a motion's key index past its track | no crash on record; not built. The load path is bounded (`rMotionList::load` relocates in place with every count checked, and the toolkit's `lmt.parse` refuses a motion whose counts or offsets do not fit before a mod can ship it); the game's own per-frame sampling is UNMEASURED, and no guard is patched into it without a crash or a measured overrun to anchor it |
| `entity_table_overflow` | more enemies than a table holds | measured bounded (`docs/re-enemy-cap.md`: every table the enemies fill is checked where filled; `enemy_cap` stays at 64 or below, where its byte compares are exact) |

## The ragdoll body-count family (built 1.0.2)

The first four sites were guarded one at a time. But the crash is not four sites; it is one idiom the compiler
inlined all over the enemy, physics and character update code: load `bodydata = [holder+0x38]`, then read the
packed body count `[bodydata+0x68] >> 8` — without the null-check the game's own accessor does. That accessor
at `0x010805D0` is `8B 41 38 85 C0 74 07 8B 40 68 C1 E8 08 C3` at `0x010805D0`: load `[ecx+0x38]`, and if it is
null return 0. Zero — "no bodies" — is the correct answer when a ragdoll is not set
up yet, and every inlined copy that skipped the null-check walks a null pointer instead.

`tools/ragdoll_sites.py` finds every copy and writes them to `native/loader/ragdoll_sites.inc` (`--check`
compares; `tests/test_ragdoll_sites.py` checks the table against the exe, and against a fresh scan when capstone
is installed). It follows each instruction that reads `[R+0x68]` back to the one that last wrote `R`, in
straight-line code; when that is `mov R, [X+0x38]` with no `test R, R` between, and the value is the packed count,
the read is one of three shapes, 62 sites beyond the four:

- **`T` (48):** `test dword [reg+0x68], 0xFFFFFF00` (the "any bodies?" test before a walk). The live crashes of
  2026-09-27, `0x007945B4`, and 2026-10-06, `0x00794AA2` (`F7 41 68 00 FF FF FF` at `0x00794AA2`), are two of these.
- **`C` (10):** `mov reg2, [reg+0x68]` then `shr reg2, 8` (a count read feeding a loop, no test first), e.g.
  `8B 78 68 C1 EF 08` at `0x00794B01`.
- **`M` (4):** the same test with the mask kept in a register: `0x007943B0` loads `BD 00 FF FF FF` at `0x007943BB`
  (`mov ebp, 0xFFFFFF00`) and tests four walks with `85 68 68` at `0x007943D3` (`test [eax+0x68], ebp`), at
  `0x00794401`, `0x0079444E` and `0x00794474`. Three bytes leave no room for a jump, so the patch starts at the
  `xor esi, esi` before each (`33 F6 85 68 68` at `0x007943D1`), which the stub runs first. These reads come right
  after calls into `0x0088D660` and `0x0088D2D0`, whose own `T` tests of the same container fault first on a
  vanilla game; with those guarded, the walk goes on to the `M` tests, so they are needed because the family is.

The first scan (1.0.2) listed 55 sites from the byte patterns alone and was pasted by hand; it missed one `T`
(`0x00794AA2`, the Devil's Firegrove crash), two `C` (`0x00794B01`, `0x009D5D42`) and the four `M`. The
generator's other 54 reads of the count are the bottoms of walks (`mov reg2, [reg+0x68]`, an increment, `shr reg2,
8`, then `cmp i, reg2; jb` back), entered only after the walk's own guarded test; they are left as they are. Reads of
`+0x68` whose value is not shifted by 8 or masked (`0x00ED68E6`, `0x00ED6B33`, `0x00ED80FB`) belong to other classes.

Each site is guarded by a per-site trampoline in an allocated code cave (`fixes.cpp`, `ApplyRagdollFamily`), under
the same `[guard] ragdoll_bodies` key: the `M` shape's leading instruction, then `test reg, reg`, and when the
pointer is set the site's own bytes run unchanged; when it is null the guard produces the game's own "no bodies"
answer without dereferencing — for `T` and `M` it leaves the flags a count of 0 would (`test reg, reg` on a null
`reg` sets ZF exactly as `test 0, mask` would, so whatever branch follows takes its no-bodies path), and for `C`
it zeroes the count register. Because the null case reproduces the flags rather than a fixed jump, no site's skip
target has to be known. Every site is verified byte-for-byte before it is patched; a site that differs is left
alone and the rest still go in. The generator refuses a table where any branch in `.text` (checked by aligned
disassembly) or any address stored in the image lands inside a patched span past its first byte.

The engine harness runs three of the game's walks on fake ragdolls: unguarded, `0x00794A90` stops at `0x00794AA2`
(the 2026-10-06 crash), `0x00794AF0` at `0x00794B01` and `0x007943B0` at `0x0088D665`; guarded, each walks a set-up
ragdoll as the game does and one without body data not at all (counted: one hit each, six for `0x007943B0`'s four
`M` and two `T` reads); with only the `M` sites put back, `0x007943B0` stops at `0x007943D3`. In game: UNKNOWN until
played; what to look for: `loader.log` says "62 more body-count walks made safe", and a horde fight or a kill at
Devil's Firegrove leaves no `crash-*.txt` (a guarded walk is a `ragdoll_bodies` line in `riftstone_error.log`).

**Why a ragdoll has no body data** (2026-10-06, read in the exe; which way it happens in game: UNKNOWN). The body
data is the ragdoll's resource, put in place by the setup at `0x01083260`. An enemy's `uRagdollExt` is made at
`0x00793D23` (its vtable stored with `C7 06 D8 F6 59 01` at `0x00793D35`) and given its resource through `0x01119B00`
(`E8 93 5D 98 00` at `0x00793D68`), which calls the setup (`E8 FD 96 F6 FF` at `0x01119B5E`). The setup tears the old
one down first (`89 6E 38` at `0x0108204E` clears it), refuses a resource with no bodies (`F7 47 68 00 FF FF FF` at
`0x01083281`), stores the resource (`89 7E 38` at `0x010832A9`), then takes four arrays sized by the body count from
the allocator `uRigidBody`'s DTI names (`68 78 39 8D 01 E8 12 2B C7 FF` at `0x01083304`). That allocator is entry
`(DTI+0x18 >> 23) & 0x3F` of the table at `0x01876628` (`8B 48 18 C1 E9 17 83 E1 3F 8B 04 8D 28 66 87 01` at
`0x00CF5E24`); the DTI registers index 0 (`6A 00 6A 00 6A 00 68 70 01 00 00 68 BC 6D 8D 01 68 04 EF 43 01 B9 78 39 8D 01` at
`0x01384EC0`). When any of the four arrays is refused, the setup frees the others and clears the body data again
(`89 46 4C 85 C0 75 1F` at `0x0108333B`, `C7 46 38 00 00 00 00` at `0x01083354`, `89 7E 38` at `0x0108347D`). The
ragdoll then stays without it until it is set up again, and nothing in the walks notices. Index 0 is only what the
DTI registers: at start-up WinMain's class map gives uRigidBody (through its ancestor cUnit) slot 12, the "Unit" pool,
an MtScalableAllocator of a fixed 64 MiB (`68 00 00 00 04` at `0x00740669`) that every enemy, player, model, rigid
body, ragdoll and AI object shares and that never grows (`docs/re-memory-pools.md`). The owner's session points that way:
`enemy_cap.log` went from 42 enemies at 22:32:51 to 62 of 64 at 22:33:19. The guard's first hit was at 22:33:18
(`0x007949E0`), and it answered about 19,000 walks in 7 s (about 3,000 a second, dozens of ragdolls) before the death
action reached the unguarded `0x00794AA2`. So the lead is a pool running dry at 60 enemies at once. A resource with no
bodies, or a walk after a teardown, would leave the same null. The `pool_cap` plugin (`native/plugins/pool_cap`)
makes the Unit pool 256 MiB by default (and four other pools four times the game's) before WinMain builds them, and
its log counts every refusal, naming each one the setup asked for: so the next session with many enemies says
whether the pool ran dry. Its harness refuses the game's own setup at its first and third arrays and, as a Goblin's
19-body ragdoll, at its first 80 bytes once the small heap is full (SIMULATED); in game UNKNOWN.

## Broken loose textures (built 1.0.2)

`missing_textures` answers a texture the game asks for that is not there; `broken_textures` answers one that is
there but does not fit its own file — a mod's truncated or garbled `.tex`, the kind of imperfect asset the brief
of 2026-09-27 asked the engine to survive. When the game opens a loose `.tex` (from the overlay or `nativePC`),
the file hook (`loader.cpp`, `GuardBrokenTex`) checks it against rTexture's header (`fixes.cpp`,
`TextureFileBroken`): the magic `TEX\0`, a known revision (0x099 Dark Arisen / 0x09D Online), a non-zero mip
count, depth and size, room for the offset table, and every mip offset inside the file. Only unambiguous
breakage is caught — never a valid texture — so a good mod texture is never replaced. A broken one is answered
as if it were missing: the resource's own bytes from its archive when the game has them (`from_archives`), else
the neutral stand-in. It is on by default (like `missing_textures`) and only touches loose `.tex` opens, which
are few (mod overrides); the archived textures the game loads in bulk do not pass through it. Harness: the
validator on a valid, a truncated, a wrong-revision, a wrong-magic and a too-short file, and a valid cube map
(whose faces are not one flat run of offsets, so they are not range-checked; the earlier version flagged
`DefaultCube_CM.tex` in game on 2026-09-28 and swapped the reflection for the stand-in).

## The effect-system dispatch (built 1.0.2)

`0x010CBFA0` is a small dispatch the effect system calls with `a1`, `a2`, `a3` on the stack and `ecx = [a2+4]`
(both callers set it, at `0x010CFDCD` and `0x010CFE54`): it reads a tag byte `0F B6 40 03` at `0x010CBFA4`
(`[a1+3]`) and returns a field chosen by it. When the compat layer converts an Online effect, that block is laid
out as Online lays it, so `a1` points nowhere -- not null, so a null-check would miss it -- and the tag read
faults (three crashes on 2026-09-26). A range check cannot tell the garbage pointer from a valid one, and a
per-call readability probe would cost on a hot path for no benefit when the block is sound.

`[guard] particles` instead reimplements the function (`fixes.cpp`, `EffectDispatchImpl`, traced byte for byte:
the five tag cases and the default, with `ecx = [a2+4]`) inside a structured exception frame
(`EffectDispatchGuarded`), and detours `0x010CBFA0` to it. A sound block dispatches exactly as before -- the
harness runs the game's own `0x010CBFA0` and the reimplementation on the same fake blocks for every tag and they
agree -- at no per-call cost, since the exception frame is free until it fires. A block that faults is contained
and the function returns the game's own default (`[a3+0x14]`, the same as an unrecognised tag) instead of
crashing. Only this one function's reads are wrapped; no other module's exceptions are touched (the process-wide
handler this page rejected would see them all). In game: UNKNOWN until played.

## The map's GUI text field (built 1.0.2)

The world map took the game down at `0x00606628` in the owner's session of 2026-09-27 22:56, with 13 Portcrystals
placed (10 in the save, 3 back from the sidecar) and one named. `0x006065E0` sets a GUI text field from a
looked-up string: it calls the string lookup `0x00607960` -- which returns null when the string is not in
sGameSys's table (`8B 15 BC A4 8F 01` at `0x00607960`, then three fields it null-checks) -- and then walks the
result as a C string (`8A 11` at `0x00606628`: `mov dl, [ecx]`). Its own null-check tests the wrong pointer:
`8D 8F D0 76 0A 00` at `0x00606619` computes sGameSys+0xA76D0 (the Arisen's cPlayerInfo, an address that is
never null) and `85 C9` at `0x0060661F` tests *that*, not the string, so a null lookup walks address 0. A
Portcrystal or place name the game has no label for -- what extra Portcrystals past the vanilla ten can carry --
is such a null.

`[guard] gui_text` (on unless 0) replaces `8B C8 8D 71 01` at `0x00606621` (`mov ecx, eax; lea esi, [ecx+1]`,
where `eax` is the looked-up string) with a trampoline: a non-null string is walked unchanged, and a null one
becomes the empty string, so the field shows nothing (an unlabelled icon) -- the engine's own "no string" case
-- instead of crashing. The trampoline leaves `ecx` non-null, so the `je` that follows takes the same branch it
always did. Byte-verified against build 2364871; the harness runs the thunk's two paths on a real and a null
string. A map label that is only blank, rather than the name a mod meant, is a separate matter: the name must
resolve to a message the loaded game actually has (`riftstone portcrystals add --name` writes it into a
`map_placelist` mod that has to be installed). In game: UNKNOWN until played.

## A raised sun shadow map past the GPU (built 1.0.2)

This guard is not a crash on record; it is the loader keeping its own shadow feature honest. `[render]
shadow_map_size` raises the sun shadow map: `getShadowMapSize` (`0x00DA97D0`) reads the size from a three-entry
table by ShadowQuality (`8B 04 85 BC 92 42 01` at `0x00DA97DA`, so the table is at `0x014292BC`: {512, 1024, 2048}
for LOW / MEDIUM / HIGH), and the feature rewrites the HIGH entry (`docs/re-shadows.md`). That rewrite happens at
start-up, before the Direct3D device exists, so it cannot know what the GPU will allow. A HIGH value past the
device's largest texture -- 8192 on a card that tops out at 4096, say -- makes the shadow buffer's `CreateTexture`
fail, and the depth pass goes down with it.

`[guard] shadow_buffers` (on unless 0) closes that at the one moment the limit is known. The loader already wraps
`CreateDevice` (`live.cpp`, for frame timing and the pool counters); `GraphicsDeviceCreated` now reads the device's
`MaxTextureWidth` / `MaxTextureHeight` and hands the smaller to `FixesDeviceCreated` (`fixes.cpp`). If -- and only
if -- the loader's own raised HIGH entry is larger, it is bounded down to the largest multiple of 32 the GPU
allows (the lamp maps, half the sun, follow), counted as a `shadow_buffers` hit and logged. Vanilla's 2048 is under
every real device's limit, so a game the loader did not touch is never changed, and a raised size the GPU can make
is left exactly as asked. The device hook is installed for this even when `[live] frame_stats` is off, so the bound
holds either way. Harness: the raised table at start-up, a GPU large enough (unchanged), a 4096-px GPU (bounded to
4096, lamps 2048, one hit), a smaller GPU (bounded again) and the guard off (left for the owner). In game: UNKNOWN
until played.

## Counters raised before their bound (a scan, 2026-10-07)

The collision crash came from a count raised before its bound and never lowered when the bound refused. A
static scan of DDDA.exe looked for the same pattern anywhere else. It is anchored on every `cmp x, imm` with a
bound of 4..0x100000 followed by a conditional jump. It decodes the instructions before each one and keeps those
whose compared value was just raised: in memory, by `InterlockedIncrement`, by `xadd`, or loaded right after an
`inc`. It then looks at both successors for a lowering. It flagged 32 counters, each checked by hand:

- Every `InterlockedIncrement` against 800 (`+0x34` from 18 sites, and the same counter reached through other
  base registers at `+0x0`, `+0x9`, `+0x84`, `+0xD4`, `+0xE4` and `+0x10E0`) is one of `collision_cap`'s inlined
  copies of `getEntryNode`.
- The one bound-800 hit outside that list is a `switch` on an id, not a count: `3D 20 03 00 00 7F 3B` at
  `0x00BA8C70`.
- The other flagged counters are loop indices and fill counts. None of them is a counter the game raises past a
  table and then trusts.

No second overshoot was found. The scan misses a value that is moved to another register before its compare, so
this is evidence, not proof.

## The hang of 2026-10-06 14:42 (no frame for 250 s)

The owner's `hang-20261006-144247` came 300 s into a session, with the game window in front. The game ran
through ENB's `d3d9.dll` chained to DXVK on NVIDIA's Vulkan driver, with OBS's game capture, TikTok LIVE Studio
and the Steam overlay hooked in.

The main thread was not stuck. Its stack in the `.dmp` (read through the memory list) holds the return address
of the main loop's frame call (`8B 42 18 FF D0` at `0x00DF0E51`, sApp's vtable `+0x18`) and the frame limiter's
`Sleep` (`FF 15 5C D1 39 01` at `0x00DBFDBD`, a 4 ms wait). So it was running whole frames and pacing them, while
no `Present` reached the loader's counter.

The game's loop (`0x00DF0D20`) has two paths that run no frame. Neither is the one the thread was on:

- **The game believes it is not active.** `80 7E 20 00 75 40 84 C0 75 3C` at `0x00DF0DE9` tests sApp`+0x20` and
  the active byte sApp`+0x2566`. When both are 0, the loop sleeps 1 ms and draws nothing
  (`6A 01 FF 15 5C D1 39 01` at `0x00DF0E00`). `WM_ACTIVATEAPP` with false clears the active byte
  (`C6 81 66 25 00 00 00` at `0x00DF0F0B`).
- **The render device's lost flag.** `80 B9 68 3E 43 00 00 75 B7` at `0x00DF0E40` skips the frame while
  sRender`+0x433E68` is set.

In the dump, DXVK's present thread was inside the Steam overlay's present hook, which OBS's capture hook had
called (`gameoverlayrenderer.dll` over `graphics-hook32.dll` over `d3d9.dll`), waiting in a `win32u` system
call. That is the lead: frames made but not shown, held in a hooked present chain. The cause is UNKNOWN. It
happened once, and the overlays are not Riftstone's to change. A hang report's `.dmp` read with
`riftstone threads` shows the same picture for any later one.

A lost device is the other way to draw nothing. The game's present function compares Present's result with
`D3DERR_DEVICELOST` and sets reset request 0x10 (`3D 68 08 76 88 75 09 83 8E 54 3E 43 00 10` at `0x00DAEE55`).
With a request set, the reset check (`0x00DAF7C0`, every pass of the main loop) asks TestCooperativeLevel and
returns while it says the device is lost (`8B 51 0C 50 FF D2 3D 68 08 76 88 0F 84 C0 04 00 00` at `0x00DAF834`).
Any other answer leads to the reset. Since 2026-10-07 a hang or snapshot report has a "graphics device"
section, so these cases read differently:

- the last Present's result, and how long it has been failing;
- TestCooperativeLevel's calls and last result, and Reset's;
- the window's mode, and whether it is in front;
- the game's own gates: sApp `+0x2566` and `+0x20`, sRender `+0x433E54` and `+0x433E68`.

A lost device, a device the driver stopped, a game that counts itself inactive, and a present chain holding
the frames (every result fine, every gate open) each show up differently there. The loader harness's
`hanglost` runs the lost-device case: a Present through the loader's hook says `D3DERR_DEVICELOST`, then the
game only asks TestCooperativeLevel. Which case the 14:42 hang was is UNKNOWN until a hang under this loader
is reported.

## Crash reports from several threads at once (loader, 2026-10-07)

At Gran Soren the game's job threads faulted together, and only one of the nine minidumps of those three
crashes came out whole. The loader now writes reports one at a time, and holds every crashing thread until the
last report is written (`docs/runtime.md`, "Crashes on several threads at once"; the harness's `crashrace`).

## The empty hang report of 2026-09-28 12:48 (loader, 2026-10-07)

`hang-20260928-124832.txt` in the game's logs is 0 bytes: a hang report was begun and never written to. Windows'
Application log has the reason. At 12:47:49, DDDA.exe crashed (Application Error, event 1000; the fault at offset
`0x00079C44`, which is `0x00479C44`, the collision sweep of `docs/re-collision-cap.md`). Windows Error Reporting
logged it at 12:47:51 (event 1001). The loader's own report of that crash is `crash-20260928-124748`. There is no
Kernel-Power 41 or 6008 event, so the machine did not stop. The game drew no more frames while Windows Error
Reporting held it, and 43 s after the crash the hang watch began its report as the process was being ended.

Two other causes were tested and ruled out:

- **The loader lock.** A main thread that holds the loader lock does not stop a report on this Windows (11, build
  26200). The harness's `hanglock` holds the lock for 6 s, and the hang report comes out whole: its sections, and
  the main thread named by its module (`ntdll.dll+0x...`). The case stays in the harness as a guard.
- **A system freeze.** The event log has no such event.

**The change.** A crash that goes past every filter without one recovering it (no `EXCEPTION_CONTINUE_EXECUTION`)
ends the game. From then on, a stop in frames is that ending, not a hang. The hang watch logs "no frame for N s
since a crash nothing recovered (the game is ending); no hang report" and writes nothing. If frames come again
after the crash, the game lived on, and the next stop is reported as a hang.

The harness's `crashhang` checks both halves:

- A thread crashes. The crash goes through the loader's filter to the game's, and the process lingers 4.5 s
  without a frame: one crash report, no hang report, and the log line.
- Then 30 frames on the same device, and another 4.5 s without one: one hang report.

In game: UNKNOWN.

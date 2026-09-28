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
| `0x007945B4`, and 54 more copies of the same idiom | the owner's session of 2026-09-27 (another goblin horde at Gran Soren) | the same read one function over: `8B 46 38` at `0x007945AB` loads `bodydata = [holder+0x38]`, then `F7 40 68 00 FF FF FF` at `0x007945B4` reads `[bodydata+0x68]` with no null-check | `ragdoll_bodies` family (built) |
| `0x010CBFA4` in Dark Arisen, an effect-system tag dispatch | three times on 2026-09-26 (converted Online effects, the compat layer) | read the tag byte `0F B6 40 03` at `0x010CBFA4` through `a1`, a parameter block in the source game's layout, so `a1` points nowhere (`0x8227CFE0`) and is not null | `particles` (built) |
| `0x00606628`, a GUI text field | the owner's session of 2026-09-27 22:56 (the world map, with 13 Portcrystals placed, one named) | `8A 11` at `0x00606628` walks a null string: `0x006065E0` looks a string up (`0x00607960`, null when it is not in sGameSys's table) and its own null-check tests the wrong pointer -- `8D 8F D0 76 0A 00` at `0x00606619` tests sGameSys+0xA76D0, an address that is never null | `gui_text` (built) |
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
| `ragdoll_bodies` | a ragdoll walked before its bodies are set up: four bespoke sites, then the whole family a scan found (55 more) | built (1.0.2) |
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

A scan of DDDA.exe (build 2364871) for the idiom found 55 more copies beyond the four, in two shapes:

- **`T` (47):** `test dword [reg+0x68], 0xFFFFFF00` (the "any bodies?" test before a walk; `reg` from `[X+0x38]`,
  never null-checked). The live crash of 2026-09-27, `0x007945B4`, is one of these.
- **`C` (8):** `mov reg2, [reg+0x68]` then `shr reg2, 8` (a count read feeding a loop, no test first).

Each is guarded by a per-site trampoline in an allocated code cave (`fixes.cpp`, `ApplyRagdollFamily`), under the
same `[guard] ragdoll_bodies` key: `test reg, reg`, and when the pointer is set the site's own bytes run
unchanged; when it is null the guard produces the game's own "no bodies" answer without dereferencing —
for `T` it leaves the flags a count of 0 would (`test reg, reg` on a null `reg` sets ZF exactly as
`test 0, imm` would, so whatever branch follows takes its no-bodies path), and for `C` it zeroes the count
register. Because the null case reproduces the flags rather than a fixed jump, no site's skip target has to be
known. Every one of the 55 is verified byte-for-byte before it is patched; a site that differs is left alone
and the rest still go in. Two sites the raw opcode scan flagged as possible branch targets were checked by
aligned disassembly and are false positives (their loops' back-edges land after the patched bytes), so all 55
are safe to overwrite. The eight `C` sites and a handful of `T` sites have no crash on record; they are the same
bug and are guarded for the same reason. In game: UNKNOWN until played.

The remaining standalone reads the scan surfaced but did not guard (the `39/3B` compare forms of the count, and
the `mov;shr` reads that a `T` test in the same walk already dominates) are not separate crash entries: a walk
whose entry `test` is guarded never reaches its own later reads with a null pointer.

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

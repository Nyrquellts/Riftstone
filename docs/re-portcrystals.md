# More Portcrystals: where Dark Arisen keeps them

Measured 2026-09-27 on build 2364871 and built the same day as the `portcrystals` plugin
(`native/plugins/portcrystals`): 15 placed Portcrystals instead of ten, `slots` 10..32. The limit is not one number:
the game's own list, its save, three routines unrolled for exactly ten, and the map's icons are all sized for ten. So
the plugin moves each of them somewhere bigger, the way `enemy_cap` does (`docs/re-enemy-cap.md`). Every byte quoted
here is re-checked against the exe by `tools/doc_claims.py`. In game: UNKNOWN until played.

## The save

- `cSAVE_DATA_PL`'s property list (0x00491F20) registers `Anchor_Area` (u32) at +0xC1F4 and `Anchor_Pos` (vector3,
  16 bytes) at +0xC220, ten each:
  - `8D 8F F4 C1 00 00` at `0x004926A3`
  - `C7 44 24 24 0A 00 00 00` at `0x004926C8`
  - `8D 87 20 C2 00 00` at `0x004926ED`
  - `C7 44 24 24 0A 00 00 00` at `0x0049270E`
- In `DDDA.sav`'s XML they are `Anchor_Area` (u32, count 10) and `Anchor_Pos` (vector3, count 10). An area of 0 is a
  free slot; 100 is the overworld.
- A save holds them in both of its `mPl` blocks. They were identical in the owner's save on 2026-09-27, where all ten
  held 100. `mLastClearPlayerData`, also a `cSAVE_DATA_PL`, shows none.

## The game's own list (sGameSys)

`sGameSys` (`[0x018FA4BC]`) keeps the live list:

- areas at +0xBE378, 10 × u32;
- positions at +0xBE3A0, 10 × 16 bytes (x, y, z, w), reached as `(i + 0xBE3A) << 4`;
- the next field starts at +0xBE440.

`sGameSys` is 0xBE470 bytes: `68 70 E4 0B 00` at `0x0132BA76` (its type info's registered size), at `0x0041C04E` (the
game's allocation) and at `0x00437B1B` (the type info's `newInstance`).

**Loading, save → game** (0x004947B0):
- `8D 96 F4 C1 00 00` at `0x00494BB0`
- `89 9C 87 78 E3 0B 00` at `0x00494BCA`
- `05 3A BE 00 00` at `0x00494BD7`
- the ten-slot loop: `83 F9 0A 72 C1` at `0x00494BFA`

**Saving, game → save** (0x00493AA0):
- `8D 9F F4 C1 00 00` at `0x00493D7D`
- an index clamp: `80 FA 0A 72 07` at `0x00493D90`
- `8B 84 86 78 E3 0B 00` at `0x00493D9F`
- `05 3A BE 00 00` at `0x00493DCA`

**Placing one**, the Portcrystal action (0x00B12C20). It takes the current stage (`8B 7A 34` at `0x00B12C9A`) and looks
for the first free slot:
- `8D 8A 78 E3 0B 00` at `0x00B12C9F`
- `83 39 00 74 24` at `0x00B12CA5`
- the limit: `83 F8 0A 72 F1` at `0x00B12CAF`, `B8 0A 00 00 00` at `0x00B12CB4`, and `83 F8 0A 72 13` at `0x00B12CBF`
  (no free slot: the placement is refused)

It claims the slot with `89 BC 82 78 E3 0B 00` at `0x00B12CCE`. The crystal it puts in the world keeps its slot number
in a byte: `88 88 A5 29 00 00` at `0x00B12E13`.

**Picking one up** (0x00B12E90):
- `3C 0A` at `0x00B130B2`
- `89 AC 81 78 E3 0B 00` at `0x00B130BF`
- `05 3A BE 00 00` at `0x00B130CE`

**Counting the placed ones** (0x0044D170) is unrolled for exactly ten: from `32 C0 83 B9 78 E3 0B 00 00` at `0x0044D170`
to `83 B9 9C E3 0B 00 00` at `0x0044D1D5`. It has six callers; two compare its answer with ten (`3C 0A` at `0x0066CDB7`
and at `0x00B12B3F`).

**Clearing all ten** (0x0043C580, 10 callers) is unrolled too: `89 BB 78 E3 0B 00` at `0x0043CA89` to
`F3 0F 11 83 3C E4 0B 00` at `0x0043CC03`. So is the constructor's setting of each position's w (0x00437BE0):
`F3 0F 11 85 AC E3 0B 00` at `0x0043847F` to `F3 0F 11 85 3C E4 0B 00` at `0x004384C7`.

**The Ferrystone's destinations.** 0x0068FF00 maps a list entry to a slot:
- `80 F9 0A 73 0D` at `0x0068FF1A`
- `83 BC AE 78 E3 0B 00 00` at `0x0068FF22`
- `80 F9 0A 72 E3` at `0x0068FF37`

0x0068FF50 gives the entry's position: `83 BC 8A 78 E3 0B 00 64` at `0x0068FFA3`. The position accessors are
0x0044D0D0 and 0x0044D140 (`add reg, 0xBE3A`).

**Stage load** (0x00501020), which puts the crystals in the world:
- `BF A0 E3 0B 00` at `0x00503024`
- `C7 44 24 50 78 E3 0B 00` at `0x00503029`
- `80 7C 24 13 0A` at `0x00503031`

**Roles still UNKNOWN:**
- 0x00494D80: `80 F9 0A` at `0x00494E10`, `89 AC 86 78 E3 0B 00` at `0x00494E1A`, `83 F9 0A` at `0x00494E4A`. Another
  copy into the live list, perhaps from the save's second `mPl`.
- 0x004FBD50, a reader by index: `80 F9 0A` at `0x004FBD79`, `B9 0A 00 00 00` at `0x004FBD7E`, `8B 8C 91 78 E3 0B 00`
  at `0x004FBD9B`.

## The map

The map screen, where a Ferrystone picks its destination, keeps one icon per placed crystal at +0x3AC of its UI object:
- 0x0068FB00 makes the icons (`8D 9A AC 03 00 00` at `0x0068FB2F`, `89 33` at `0x0068FB5B`);
- 0x0068FD90 places them each frame (`8D 87 AC 03 00 00` at `0x0068FDB9`).

There is room for exactly ten: another array starts at +0x3D4 (`8D BC 93 D4 03 00 00` at `0x0068AD6B`). The UI class,
its size and its allocation are not measured yet.

## Why raising the compares alone breaks the game

With only the compares changed to 15:
- slots 10 to 14 would write their areas over the first positions, and their positions over the fields after +0xBE440;
- the save keeps ten;
- the count and the clear still see ten;
- the eleventh icon lands on the map's +0x3D4 array.

## Measured while building

- The roles left UNKNOWN above:
  - 0x00494D80 is a second load copy, from a save's data into the list.
  - 0x004FBD50 is the Ferrystone jump itself. A destination of type 999 (`81 38 E7 03 00 00` at `0x004FBD6E`) is a
    placed crystal, and its slot's stage becomes the one the game goes to.
- The Ferrystone's list (0x0068FB70) draws a window of five rows (`83 F8 05` at `0x0068FCCD`) over the fixed
  destinations and then the crystals. It has no per-crystal storage, so more crystals only scroll further.
- sGameSys's constructor publishes the instance (`89 2D BC A4 8F 01` at `0x004384CF`) right after its ten w stores.
  The plugin therefore finds `[0x018FA4BC]` still 0 when it loads early enough.
- The clear's run is not all stores: `8B 0D F0 A4 8F 01` at `0x0043CB6D` sits between slot 5 and slot 6, and the code
  after the run needs it. So the plugin replaces slots 0-5's stores only; slots 6-9's still run, on the old list.
- The map is `uGUIMap`, 0x960 bytes:
  - allocated with `68 60 09 00 00` at `0x0067FC8B` and at `0x0067FCBB`;
  - registered with the same size at `0x0133AFE6`;
  - its constructor sets the ten icon pointers in one run from `89 96 AC 03 00 00` at `0x00680085`.

  Nothing else in the class touches the icon block.

## The plugin

`portcrystals` works the `enemy_cap` way. `tools/portcrystal_sites.py` generates `src/sites.inc` and checks every entry
against the exe.

1. **The list moves.** `sGameSys` grows by 0x280 (the three `push 0xBE470`), and the list moves to its tail: areas at
   +0xBE470 (room for 32), positions at +0xBE4F0 (32 × 16 bytes).
   - The displacements move: areas by 0xF8, positions by 0x150.
   - The index base 0xBE3A becomes 0xBE4F.
   - The 16 slot-bounding compares become N (`slots`).
   - The save's own ten-slot loops keep 10, as do three fallbacks that are a stage number, not a slot count.

   That is 46 instructions in all.
2. **The unrolled routines are replaced.** The constructor's stores, the clear, the count and the map's icon run
   become calls to the plugin. Each call keeps every register, the flags and xmm0-7: the save copy keeps a value in
   xmm3 across its hook.
3. **The map grows.** `uGUIMap` grows by 0x80 and its icon block moves to its tail (+0x960).
4. **The save sidecar.** The save keeps its ten exactly as the game writes them.
   - When the game builds save data (a hook at `0x00493D7D`), the slots past ten go to `riftstone\portcrystals.bin`.
     Each record is keyed by a fingerprint of the ten (FNV-1a 64 over each slot's area, x, y, z bits).
   - When the game loads a save (hooks at `0x00494BAB` and `0x00494DFD`), the record with that fingerprint fills
     slots 11-N, and with none they are empty.
   - Reloading an older save, the save's two `mPl` copies, or another Steam account therefore each get their own
     crystals. `riftstone.portcrystals` reads and writes the same file.
   - Without the plugin the crystals past ten are not in the game. The next save then forgets them, and with them
     the Portcrystal items they stand for, so pick them up before lowering `slots` or removing the plugin.
5. **Proof.** `test/run_tests.py` maps DDDA.exe into a stand-in process and runs the patched code on a fake
   sGameSys, save data and map. It covers:
   - the constructor, clear and map runs;
   - the real count, position get and set;
   - the free-slot search: the eleventh crystal takes slot 11, and a full list refuses;
   - the pick-up;
   - the Ferrystone's destination and position;
   - the save copy and both load copies through the hooks, with and without a record;
   - a reload from the file in a second process;
   - refusal of an altered instruction, or of sGameSys built too early.

   It runs at 15, 32 and 10 slots and with settings out of range: 243 checks on 2026-09-27 (with the names below).
6. **Names.** The Ferrystone's list and the map name each crystal by where it stands: the place-name function
   `0x004541B0` (ecx the position, edx the manager `[0x018FA4CC]`; the list calls it with
   `8B 15 CC A4 8F 01 8B C8 E8 79 45 DC FF` at `0x0068FC2A`) looks the position up in its table of rectangles and
   answers a message of `id/DDN/message/common/map_placelist_<language>.gmd` (277 lines; 37 is "The Bluemoon Tower"),
   or 0x43 when no rectangle holds it (`B8 43 00 00 00 81 C4 40 01 00 00 C3` at `0x00454681`). It answers -1 while
   its manager's place data is not loaded: `83 C8 FF 83 BA 3C 2B 00 00 00` at `0x004545B4`. The numbers read as the
   list says: 0x43 is "Gransys", and the rectangles' are regions (0x44 "Verda Woodlands", 0x45 "Manamia Trail", 0xDA
   "Devilfire Grove"). The rectangles are the open world's, so a crystal on another stage gets an open-world name:
   for the Bluemoon Tower's summit crystal (stage 370 at 1539, 3911.5, 1593) the game's own code answers 71,
   "Deos Hills", 1 cm from it (the harness below).
   - The plugin hooks the function's first instruction (`sub esp, 0x140`). A position listed in `[names]` of
     `portcrystals.ini` ("XXXXXXXX,YYYYYYYY,ZZZZZZZZ = message", the position's float bits) gets that message,
     under the same test of `+0x2B3C` (the generator checks those bytes as `GUARD`); every other position gets the
     game's own answer. The list asks with a copy of the slot's position that `0x0044D0D0` makes float by float with
     `fld`/`fstp` (`D9 01 D9 18` at `0x0044D11B`), exact for every finite float, so the key is the slot's own bits.
   - The list keeps a crystal of any stage: it skips only empty slots (`83 BC AE 78 E3 0B 00 00 76 09` at
     `0x0068FF22`), and the map puts a crystal whose stage is not 100 at that stage's place on the world map
     (`83 BC 8A 78 E3 0B 00 64 75 1B` at `0x0068FFA3`); the jump takes the slot's stage and position.
   - `riftstone portcrystals add --stage S --at x,y,z --name TEXT` writes the crystal into the sidecar and its name
     into `[names]`; TEXT must be a line of the place list as the installed game holds it (a mod adds one with
     `riftstone text add`). The Tower Arena Test mod adds "Tower Arena Test" as message 277 in all seven languages.
   - The harness names (1539, 3911.5, 1593) with 277 and checks that its answer is 277, that a position 1 cm away
     gets the game's own (71), and that with no place data both get -1 as the game gives.

**In game (the owner's session of 2026-09-27 19:39, `portcrystals.log`):** "15 Portcrystals placed at once ... 46 sites,
4 runs and 3 hooks patched (game)"; every load of the save went through both load hooks, and the save at 19:40:24
wrote the sidecar's record for it under fingerprint `f02abd387d58ec83`, the one `riftstone.portcrystals` computes from
that save. A crystal past ten, its jump, and a named crystal in the list: UNKNOWN until played.

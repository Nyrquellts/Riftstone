# Gransys terrain: where each piece sits, and why edits "floated"

The owner's issue list has "Gransys loads all ~800 pieces at origin (in the sky)", and the community has
long said the open world cannot be redone. The measurements below explain both. Everything was measured on
build 2364871 (Dark Arisen) and client 03.04.003 (Online). `tools/terrain_proof.py` repeats every number,
and in-game results stay UNKNOWN until someone plays an edited cell.

## The cell rule (Dark Arisen, stage 100)

The open field streams its ground in 100 m cells. The terrain model of cell `st100_<m>m<n>n` is
`scr\st100\model\m<M>0\st100_<m>m<n>n` in the archive `rom/stage/stage100/split/m<M>0/n<N>0/st100_<m>m<n>n`.
It stores **X and Z from its cell's corner** and **Y as world height**. The engine adds the corner:

```
world = local + (10000 * n - 500000,  0,  10000 * m - 500000)        centimetres
```

- The number before `m` is the world **Z** cell, and the number before `n` is the world **X** cell.
- There is no rotation and no scale.
- The shipped cells are m 42..71 and n 35..63 (418 split archives, 417 terrain models).

| Measurement (`tools/terrain_proof.py`) | Cell rule | Controls (must lose) |
|---|---|---|
| 1. Static layouts (`_s00`, world space) on their cell's terrain: 5,829 placements, 60 cells | median gap **34 cm**, 82% within 1 m | axes swapped 4.0 m, mirrored 4.7 m, one cell off 10.6 m |
| 2. Cell terrain moved by the rule against the world-space area LOD models (`area_index*`): 13,736 vertices | median **74 cm**, 71% within 2 m | one cell off 14.9 m, axes swapped 10.5 m |
| 3. Every vanilla cell model | 417/417 pass `terrain check`; all 416 world-space copies are caught | the 417th cell (50m50n) has a zero offset |
| 3b. Every vanilla cell collision (the `h` and `e` meshes, 836) | the walkable `e` mesh lies on its cell's terrain in the cell frame: median gap **69 cm** over 79,245 vertices; 836/836 pass `terrain check` with no error; 832 world-space copies caught, 2 reported as undecidable | axes swapped 6.7 m |

This is why importing Gransys into Blender stacks every cell in one 100 m square at the origin, each at its
true height, so the ground looks like it hangs "in the sky". A cell's place is its **name**, not its model.

The "floating mountains" happen when a cell is edited in world coordinates and written back that way. The
engine then adds the corner a second time, and the piece lands one corner away (up to 2 km off here).
Y is not offset, so a world-space export moves sideways, not up. Pieces seen "in the air" are stacked at
the origin at their real height.

### Every other stage-100 model

| Models | Frame | Evidence |
|---|---|---|
| `st100_<m>m<n>n` terrain (`split`) | cell rule | above |
| `st100h_/st100e_<m>m<n>n_mrg00.sbc` merged collision (same archives) | cell rule | measurement 3b. Collision reaches up to one whole cell past its own (models: half a cell) |
| `st100_area*_h/_l` area LOD (`area_index00..05`) | world | bounds of 1-2 km at absolute positions; measurement 2 |
| `scr\st100\model\water\*` rivers, lakes, sea | world | bounds span several cells; the same model sits in every cell archive it crosses |
| `model\om\...` objects (`split_sub`) | placed | local to the object; the static layouts give position, angle and scale (`riftstone spawns`) |
| `fm_f*` far mountains (`fm_index00..14`) | UNKNOWN | centred on their own origin; nothing in the stage's archives names them (probably placed by the exe) |
| `fmfore_<m>m<n>n` tiles (`splitfmfore`) | UNKNOWN | cell names and cell-sized bounds, but only 33% of vertices land within 2 m of the terrain |

`area_index` and `fm_index` are **archive names**. They are not a table of per-chunk transform matrices,
and no such table was found: the cell offset comes from the cell's name.

## Dragon's Dogma Online

DDO's field (stage 0100) has **no cells**. Its terrain models are `scr\fd\model\...`, loaded from
`rom/scr/fd/sdl/ma###_m00` and `ja###_m00` (24 areas). They are stored in world space:

| Measurement | No transform | Controls |
|---|---|---|
| 4. Stage 0100's placements on its 99 field-terrain models: 12,464 of 14,163 over terrain | median gap **34 cm**, 67% within 1 m | moved 100 m in x 16.9 m, in z 17.4 m |

A DDO field model imports where it stands, so there is nothing to move. `riftstone terrain` says so for
`--game ddo`.

## The tools

```
riftstone terrain cells [--json]              every cell, its offset and archive (--json for scripts)
riftstone terrain where 47m35n                a cell, an engine name, or a world position:
riftstone terrain where 58600,42716,-45360      "lies in cell st100_45m55n: local x 8600.0, z 4640.0"
riftstone terrain check st100_47m35n.mod      is this cell model in its cell's frame?
riftstone terrain worldize st100_47m35n.mod   -> st100_47m35n.world.mod, in world coordinates
riftstone terrain localize st100_47m35n.world.mod [-o st100_47m35n.mod]   corner taken off again
riftstone terrain localize st100e_47m35n_mrg00.world.sbc                  the same for its collision
```

`check`, `localize` and `worldize` take a cell's `.mod` or its collision `.sbc`, whichever the file is.

- **Moving a model.** `localize` and `worldize` move all of these:
  - the vertex positions;
  - the header's sphere and box;
  - every group sphere;
  - every envelope's sphere, box and oriented box (the box's rotation and extents stay).

  Every other byte stays the same, and the model keeps its size.
- **Moving a collision mesh** (`sbc.py`, the layout read from `DDDA.exe`'s loader, `docs/formats.md`):
  - the file's box, each part's box and each tree's root box;
  - every lane of every tree node (min and max per axis, four lanes a node, empty lanes included: they
    hold real boxes in every vanilla file);
  - every vertex.

  Triangles store no plane distance (normal, vertex numbers, material, attributes), so they stay byte
  for byte, as do materials, leaves and the node masks. Every other byte stays the same.
- **Which files.** Only bone-less models whose vertex formats store float positions, which covers all 417
  vanilla cell models, and collision meshes with 4-wide trees, which is every one of the game's 1,496.
  Anything else (a binary-tree or grid collision, Online's revision 0x77DF43D8) is refused, not guessed.
- **Precision.** `localize` of a world-space file is exact: the corner is a multiple of 16 cm, so the
  difference is a float. `worldize` rounds each position to float32, at most 0.0078 cm in Gransys.
  `worldize(localize(W)) == W` byte for byte on all 417 cell models and all 836 cell collisions.
  The other way round, a cell piece moved into the world and back (`localize(worldize(F))`) is F only
  to within that rounding: every value comes back within 0.0078 cm, but byte for byte only 1 of the 417
  cell models and 29 of the 836 cell collisions do (`tools/terrain_proof.py`, 2026-09-26).
- **Build guard.** A Dark Arisen mod whose cell model or cell collision is in world coordinates **does not
  build**. The build says so and names `terrain localize`. It also fails when vertices leave their own
  bounds: a model's box, or a collision part's box and the file's box. The engine culls and tests by
  those boxes, and every vanilla file keeps its vertices inside.

  Only reported by `terrain check`:
  - reaching further past the cell than any vanilla file (models half a cell, collision a whole one);
  - a layout Riftstone cannot read;
  - bounds that fit both frames. That happens for a small piece in a cell next to 50m50n, and for one
    vanilla collision, 50m51n's `h` mesh.
- **Proof.**
  - Offline: `tests/test_terrain.py` and `tests/test_sbc.py`.
  - `check_corpus --only sbc`: all 1,496 collision files follow the layout to the last byte, and a move
    by a cell corner changes only positional floats (39.7 million of them). Moved back, every value is
    within float32 rounding of where it was (half a float32 step of the moved value plus half a step of
    the value moved back; the largest change is 0.0156 cm, and 76 of the files come back byte for byte),
    and moving a second time gives the same bytes as the first (2026-09-26).
  - `tools/terrain_proof.py` checks the same on every cell model and cell collision: largest change
    0.0078 cm, and moving twice gives the same bytes.
  - The `terrain` fuzz target checks, for a model or collision: it moves by exactly the corner, nothing
    else changes, `localize` gives the original back within float32 rounding and moving that into the
    world again gives the same bytes, and a moved cell piece cannot pass `check`.

### Editing a cell in Blender (Albam)

1. Import the cell's `.mod`. `riftstone terrain cells --json` gives its offset in game units (cm, Y up).
   Convert it the way your importer converts coordinates.
2. Either **move the object** by that offset and edit in place, without applying the transform: the mesh
   stays in the cell's frame and exports correctly. Or edit a `worldize`d copy and run `terrain localize`
   on the exported file.
3. Do the same for the cell's collision, the two `.sbc` meshes in the same archive (Albam imports and
   exports `.sbc`). Either keep them in the cell's frame, or run `terrain localize` on a world-space
   export. Edited ground without matching collision means walking on the old surface.
4. Build the mod. The guard refuses a cell model or collision still in world coordinates.

## What this does not give you

- **New cells.** Whether the engine would stream a cell the game does not ship is UNKNOWN.
  - Three 168-byte regions of `DDDA.exe`'s `.data` (0x018228E8, 0x01822990, 0x01822A38) are the cell
    bitmaps for `split`, `split_way` and `split_sub`; the streamer loads a cell only when its bit is set.
  - The bit of cell (X, Z) = (n − 30, m − 40) is 33·X + Z, most significant bit first in each 32-bit
    word. Read that way, each bitmap equals the shipped cells exactly (418, 366, 333). The other 24 set
    bits (442, 390 and 357 in all) are the padding after bit 1,319 (`docs/re-engine-audit.md`).

  Plan new ground **inside** the shipped cells (m 42..71, n 35..63), which is data only. Ground beyond
  them probably needs a native patch.
- **The far mountains and fore tiles** (`fm_f*`, `fmfore_*`): their placement is UNKNOWN.
- **Editing collision triangles.** Riftstone moves a collision mesh; it does not rebuild one. After an
  edit, its trees must be rebuilt by the tool that edited it, such as Albam.
- **In game:** no edited cell has been seen in game yet. What is proven is where the engine's own data
  puts each piece. That an edited cell shows up there is UNKNOWN until played.

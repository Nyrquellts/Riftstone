# Online's equipment dye, baked for Dark Arisen

Dragon's Dogma Online colours its weapons and armour when it draws them: a colour map, a mask and three
colours per material, chosen per item and changed by dyes. Dark Arisen has no such shader, so an Online
armour ported with `riftstone port` showed its undyed colour map. `ddodye.py` reads how Online colours an
item, from the client's own files, and bakes that colour into the colour map Dark Arisen draws:
`riftstone ddo dye` shows an item's colours and writes its maps; `riftstone port <model> --dye <colour>`
ports an armour with its colour.

Measured on client 03.04.003 and Dark Arisen build 2364871 (2026-09-26). `tools/ddo_dye_proof.py`
repeats every number on this page (about 2.5 minutes; it reads both games and writes nothing). How a dyed
map looks in Dark Arisen is **UNKNOWN** until played.

## How Online colours equipment

### The material and its maps

Every material that dyes is class `nDraw::DDMrlStdEstObj`: 4,610 of the client's 38,953 materials (7,763
distinct `.mrl` files), in every equipment folder (`obj\ab` 1,192, `obj\wp` 971, `obj\al` 543, `obj\ah` 523,
`obj\aa` 395, `obj\ao` 258, `obj\ac` 188, `obj\wb` 166, `obj\wl` 138), NPC equipment (`obj\np` 218), a few
enemies (10) and lanterns (8). Each binds, by shader object id (JAMCRC of the name, 20 bits, as `mrl.py`
reads bindings):

| Binding | What | Id |
|---|---|---|
| `tAlbedoMap` | `<part>_NUKI`: the colour map, its alpha the cut-out (BC1 format 20 in 1,982 maps, BC3 24 in 7) | |
| `tColorMaskMap` | `<part>_CMM`: a mask in each of R, G and B (BC1 format 19, all 1,989; half the map's size in 1,829) | 0x383E5 |
| `CBColorMask` | five rows: `fColorMaskRate`, `fColorMaskThreshold`, `fColorMaskColor1..3` | 0x6F016 |
| `CBMaterial` | row 1 tints the environment map | 0x6C801 |
| a 22-row buffer | rows 0, 9 and 10 are what the shaders call `Globals__packed0/9/10`; row 10 is the specular colour and power | 0x7B2C2 (name not in the client) |
| `tAlbedoBlendMap` | the shared `obj\textures\obj_b_BM`, looked up by the view-space normal: a sheen, not a colour map | |

Every one of the 4,610 has rate (1, 1, 1) and threshold (1, 1, 0.9).

The shared "base texture" earlier notes spoke of is that sheen map. The colour map is the item's own
`_NUKI`; Dark Arisen's equipment uses `_NUKI` colour maps too (1,122 in `rom/eq`).

### The formula, from the shader bytecode

The shaders are in `sc\DX9\root` (rShaderPackage, archive `sa/DX9/root`): 1,397 pixel shaders (ps_3_0)
that keep their constant tables, so every register has its name. Ten of them sample `tColorMaskMap`.
Windows' own `d3dcompiler_47.dll` (D3DDisassemble) reads them. Their colour part, from the first of them
(registers renamed to what the constant table calls them):

```
texld r8, uv, tAlbedoMap          ; a = albedo.rgb * albedo.rgb     (linear light: the map squared)
...                               ; a = a * Globals0.yzw + sheen(view)
texld r8, uv, tColorMaskMap
add   r8.xyz, -r8, 1              ; 1 - mask
mul   r9.xyz, r8, fColorMaskRate  ; w = (1 - mask) * rate
mad   r8.xyz, r8.xxyw, -rate.xxyw, fColorMaskThreshold   ; threshold - w.r, threshold - w.r, threshold - w.g
add   r10.xyz, -1, fColorMaskColor1
mad   r10.xyz, r9.x, r10, 1       ; lerp(1, colour1, w.r)
mul   a, a, r10
...                               ; colour2 likewise, where threshold.x - w.r >= 0
...                               ; colour3 likewise, where threshold.y - w.r >= 0 and threshold.z - w.g >= 0
```

So, per channel, on the colour map squared:

    w   = (1 - mask.rgb) * rate
    a  *= lerp(1, colour1, w.r)
    a  *= lerp(1, colour2, w.g)     where w.r <= threshold.x
    a  *= lerp(1, colour3, w.b)     where w.r <= threshold.y and w.g <= threshold.z

A white mask channel dyes nothing; black dyes fully. With the rate and threshold every material has, colour 3
is the main dye (wherever blue is off and green is on), colour 2 marks leather and the like, colour 1 grime.
`ddodye.ColourMask.factors` is this formula. The proof tool runs each of the ten shaders' colour part, from
its colour-map sample to its detail-map sample, in an interpreter on 200 random inputs (colour map, sheen,
mask, rate, threshold, colours): all ten equal it. Two terms around it are not colour and are not baked: the
view-dependent sheen above, and a scene detail map (`tDDMaterialAlbedoMapEx`) the engine blends in by
`fDDMaterialFactor`.

### Where an item's colours come from

**The montage** (`.dmt`, rDDOModelMontage, 2,282 files, all version 0x11) beside each equipment model holds
its colour table. Header: magic `DMT\0`, version 0x11, kind at +0x08, counts, the number of colours at +0x18,
entries per colour at +0x1C, nine table offsets from +0x24; the colour table at the offset in +0x30 is
colours x entries of 0x60 bytes: five float4 rows, the model material's index (u32 at +0x50), 12 zero bytes.
Read in DDO.exe (the unpacked dump in `<path>`; the retail exe is encrypted on disk, so these are
checked by `tools/ddo_dye_proof.py --dump <exe>`, 10 of 10 quoted instructions, not by `doc_claims.py`):

- the loader (rDDOModelMontage vtable slot 10, 0x00A6A630) takes only `DMT\0` version 0x11;
- slot 19 (0x00A697E0), which `uDDOActorModel`'s `mEquipColorId` setter calls, takes the colour number
  modulo the number of colours (a `div`), does nothing for a montage of kind 9 unless forced, and hands the
  entries to the colour routine (0x00A69920 / 0x00A69A70);
- the colour routine finds each entry's model material by index, looks up three constant buffers by id
  (0x7B2C216E, 0x6C80120E, 0x6F016331) and copies row 0 into the specular colour (buffer 0x7B2C2 + 0xA0,
  row 10), row 1 into `CBMaterial` + 0x10 (row 1, the environment tint) and rows 2-4 into `CBColorMask` +
  0x20..0x4C: `fColorMaskColor1-3`. Rate and threshold stay the material's.

2,199 montages sit next to a model and its material; their 59,049 filled entries all name a material that
has `CBColorMask`, none one without (118 more name a material index past the model's, which the exe skips). The
material file's own `CBColorMask` colours equal one of its montage's colours in only 457 of them: the item's
colour, not the file, is what the game shows.

**Colour numbers** in use across the montages: 0-5 (1,358-1,669 files each), 6-9 (about 400), 10-15 (2,136
to 2,146), 16-29 (380-845) and 30 (7). Numbers 10-15 are the dyes, in the order the client's dye items
number them (Red, Green, Blue, Yellow, Pink and Black Dye carry item parameter 81 = 3..8; Rainbow Dye 1,
Color Restorer 2). Their mean colours over the 928 montages of 16 colours:

| Number | Dye | Mean colour (linear) | Hue |
|---|---|---|---|
| 10 | red | 0.600, 0.381, 0.355 | 6 |
| 11 | green | 0.426, 0.499, 0.384 | 98 |
| 12 | blue | 0.401, 0.439, 0.552 | 225 |
| 13 | yellow | 0.633, 0.533, 0.333 | 40 |
| 14 | pink | 0.735, 0.540, 0.617 | 336 |
| 15 | black | 0.330, 0.253, 0.298 | 325 (value 0.33) |

That a dye item leads to number 10-15 is measured on these colours, not traced in the client's code.

**The item's own colour.** The item list `etc\itemlist.ipa` (rItemList, version 0x44, 990,174 bytes) is
read completely, following DDO.exe's loader (0x00A972A0) and each item class's reader (vtable slot 5): a
header of 14 counts, a table of 25,769 u32, then 567 use, 3,354 material, 18 key, 19 job and 539 plain items,
7,158 weapons, 1,870 weapon groups, 8,570 armours, 2,190 armour groups, 338 jewels and 942 NPC equipment,
packed with no padding, each with its count of parameters. `build_itemlist` writes it back byte for byte. A
weapon or armour names its group; the group holds the model base (model tag, parts, **colour number**), the
name id (a message of `ui\00_message\common\item_name`) and who wears it (1 anyone, 2 men, 3 women).

**The model table** `etc\wepResTable.wrt` (rWeaponResTable version 11, 2,434 records, rebuilt byte for byte)
turns a model tag into the archive `armor\<name>` or `wp\<name>`, the body it fits (0 either, 1 male `_00`,
2 female `_01`) and seven references, each a type id and the JAMCRC of a resource name: the model and the
montage among them.

Every weapon and armour, checked through that chain: 23,921 (item, body) pairs land on a colour number that
has colours, 15 on an empty one (one model of thigh-highs), 34 on a montage with no colours. The items' own
colour numbers: 0 (2,443), 1 (1,660), 2 (1,918), 3 (2,672), 4 (1,966), 5 (1,588), 20 (746), 21 (60), 22 (320),
23 (2,336) and a few others. The server keeps a dyed item's colour in the item (`Color`, set by the craft
colour change).

### Dark Arisen

Dark Arisen's equipment has `_CMM` textures too, but binds them as `tSpecularMap` (1,757 bindings; 2 as
`tEmissionMap`; `DDMaterialStd` and `DDMaterialInner` materials), and none of its 76 shader packages names a
colour mask: its colours are in its colour maps. That gives a check on all of the above. 191 of Online's colour maps are Dark Arisen's own
equipment art (the same 32x32 structure, correlation above 0.9). For the 40 closest, Online's formula with the
nearest of the item's own colours comes within a median 6.8 of 255 per channel of Dark Arisen's map (undyed:
25.9), and lands on its brightness: median luminance 40.7 for Dark Arisen's maps, 68.1 for Online's undyed
ones, 39.4 baked. Dark Arisen's 1,122 equipment colour maps average 38.8 (150 of them).

## Using it

```bat
Riftstone.cmd ddo dye "Bronze Plate"
    :: the model, its colour-mask materials, the item's colour number, every colour number with colours, the dyes
Riftstone.cmd ddo dye "Bronze Plate" --colour red --out C:\temp\red
    :: the colour maps with the colour baked in, as Dark Arisen textures (--as png or dds to look at them)
Riftstone.cmd ddo dye obj\ab\ab210004\model\ab210004_00 --colour #3060c0 --out C:\temp\blue --as png
Riftstone.cmd port obj/ab/ab210004/model/ab210004_00.mod --as <a Dark Arisen armour>.mod --mod "My Mod" --dye default
```

A colour is `default` (the item's own; for a model path, the colour most items wearing it have), `material`
(the material file's own colours), a dye (`red`, `green`, `blue`, `yellow`, `pink`, `black`), a colour number,
`#rrggbb` (every dyed part; the value multiplies the colour map, which is the linear colour (v/255)^2 in the
shader's terms) or `#rrggbb,#rrggbb,#rrggbb` (colours 1, 2 and 3). An item for either sex takes `--sex female`
for the female model. `rainbow` and `restorer` are refused with an explanation: the game picks the first's
colour, and the second puts the item's own back.

**Baking** multiplies each texel by the square root of the factor, per channel: exact in Online's own
convention (its shader squares the map, multiplies, and lights the product). A negative factor counts as 0;
255 caps. The mask is read at each texel's centre, bilinear and wrapping (the mask is usually half the map's
size). Alpha, and so the cut-out, stays. Every 4x4 block, at every mip, that no dyed texel reaches keeps its
bytes; the format, size and mip count stay the map's; the revision becomes Dark Arisen's (0x99) with
`tex.attr1_for`.

**`port --dye`** checks the model and the colour before the port writes anything, then: every material the
port rebuilt whose colour map is one of the model's colour-mask maps now points at a dyed copy,
`ddo\<map>_d<crc32>` (one per colour set: materials that share a map may wear different colours, as in 962 of
the 2,199 models), written into every archive of the mod that holds the map. The material's texture table
keeps only textures a binding uses, so it lists nothing the mod no longer holds (a listed texture the archive
lacks may be looked up as a loose file, which is fatal in Dark Arisen). The undyed copy the port wrote, and the copies an earlier `--dye`
of the same model made, are removed when no material of the mod uses them any more. Materials without a mask
keep theirs.

A dyed port is shared the way every port is (`docs/legal.md`): the mod's `riftstone-sources.json` records the
port's recipe with its colour (`"dye": "red"`), never the textures. `package install` replays it on the
player's own copy of Online. That makes the same dyed maps byte for byte, which the unit tests check by
replaying a dyed port's recipe. A port without `--dye` records no colour, as before.

## What stays UNKNOWN or approximate

- **In game**: how a dyed map looks in Dark Arisen, under its lighting and the Dark Arisen material the port
  rebuilt from a template.
- Not baked: the sheen (view-dependent), the specular colour and environment tint a montage also sets (rows
  0 and 1; the rebuilt material keeps its template's), and the engine's scene detail map.
- The dye -> colour number step (10-15) is measured by the colours' hues, not traced in the client's code;
  which colour the Rainbow Dye picks is UNKNOWN.
- The mask's sampler: wrapping and bilinear at texel centres are assumed (the game samples per screen pixel).
- Gamma: Online squares the colour map in 723 of the 724 pixel shaders that read its colour (by pattern);
  how Dark Arisen decodes a colour map is not traced. The brightness agreement above says the two line up.
- A montage of kind 9 (56 files) colours only when forced: `default` uses the material file's colours, and
  whether a dye reaches it in game is UNKNOWN.
- The 15 (item, body) pairs whose colour number is empty: the game would write zeros (black dyed parts);
  `default` uses the material file's colours instead, and a number that is empty is refused.

## Proof

- `tests/test_ddodye.py` (28 tests): the formula against the shader's arithmetic written out again, pixels and
  textures baked (white masks, rate 0, wrapping, negative and bright colours, BC1 cut-out, BC3, the revision,
  refusals), the montage, the item list and the model table read and rebuilt byte for byte (refusals too),
  what people type, colours per material (default, empty and kind-9 montages, dyes, RGB), repointing a
  material, stand-in games (`tests/dye_fixture.py`: an Online armour with its material, montage, item list,
  model table and names; a Dark Arisen armour) resolved by name, id and path, baked to a folder, and ported
  into a mod with `--dye` (two colour sets on one map, the undyed copy kept while a material uses it and
  removed when none does, the mod builds), the command line, and one real armour when the client is on the PC.
- Fuzz targets `dye` (every texel equals an independent oracle of the shader's formula; alpha, shape and
  untouched blocks stay) and `dye_tables` (montage, item list, model table, typed colours). The first run
  found one slow input (a montage counting 50 million colours of no entries), fixed with a regression test;
  then 120 s: 712,584 and 79.3 million executions, 0 findings (`cli` and `port`, 60 s: 0).
- `tools/ddo_dye_proof.py` repeats every number above.

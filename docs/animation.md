# Animation: motion lists (`.lmt`), their codecs, and moving them between the games

Riftstone reads and rebuilds every skeletal-animation file of both games byte for byte, decodes every
keyframe, and ports motion lists between Dark Arisen and Online with the motion kept. Everything on this
page was measured on the installed games (`tools/check_corpus.py --only lmt`, both games) or read from
the games' own loaders; what nobody has watched in game is marked **UNKNOWN**.

| | DDDA | DDO |
|---|---|---|
| Version | 66 (all 1,002 distinct files) | 67 (all 1,402) |
| Byte-exact rebuild | 1,002 / 1,002 | 1,402 / 1,402 |
| Motions / tracks | 8,976 / 905,046 | 19,685 / 1,944,772 |
| Keyframe buffers re-packed identically | 420,900 (20.1 M keys) | 675,678 (34.9 M keys) |
| Key frame spans = frame count - 1 | every track | every track |
| Loader | `rMotionList::load` 0x00E9D330 | 0x015A4B80 (Buns' unpacked exe) |

Code: `src/riftstone/lmt.py` (container), `src/riftstone/lmtcodec.py` (keyframes),
`src/riftstone/port.py` (`convert_lmt`, `skeleton`, `skeleton_diff`), `tools/motion_compat.py`.

## What a motion list is

One `.lmt` holds up to 65,535 motion slots (empty slots are 0). A motion is a set of **bone tracks**
(one channel of one joint: rotation, position or scale, local or absolute), an **event block** (4 groups
of run-event bits over frame ranges -- what the engine fires while the motion plays) and optional
**float tracks** (scalar curves). Tracks address joints by the model's **joint id** (the byte a model's
bone record starts with), not by bone index; id 255 is the root / scene track.

## Layout (both versions)

```
file      "LMT\0"  u16 version  u16 count  u32 offset[count]           (0 = empty slot)
motion    60 bytes, each on a 16-byte boundary:
          +00 tracks  +04 track count  +08 frame count  +0C s32 loop frame
          +10 f32[4] end-frame additive position  +20 f32[4] end-frame additive rotation
          +30 flags   +34 events  +38 float tracks
track     36 bytes: u8 codec, u8 usage, u8 bone type, u8 joint id, f32 weight,
          u32 buffer size, u32 buffer, f32[4] reference value, u32 extremes (32 bytes)
events    4 x { u16 remap[32], u32 count, u32 list } -> count x { u32 bits, u32 frames }
floats    (flags >> 16) & 0x1F groups x { u8 remap[4], u32 count, u32 frames } -> count x 16 bytes
```

Flags, from the loaders and every file: `0x800000` = has events (all motions);
`(flags >> 16) & 0x1F` = float groups (bits worth 1, 2, 4, 8, 16; both loaders compute it this way);
`0x1000000` / `0x2000000` / `0x4000000` = this motion's tracks / events / floats were already turned
into pointers by an earlier motion that shares them -- the loader skips them. Bit 24 is set on exactly
the motions that reuse an earlier motion's track array (447 of 8,976 DDDA, 2,988 of 19,685 DDO) and
nowhere else, so `lmt.parse` refuses a file where it disagrees (the game would mis-relocate it) and
`lmt.build` computes it. Bit 0 is set on 19,597 DDO motions and no DDDA motion; neither loader reads it
(meaning UNKNOWN).

Writer order (what `build` reproduces): header, motion headers, then per motion: its track array (if
not already written), the extremes (from a 16-byte boundary), the buffers (4-aligned), the event block
and lists, the float block and frames. Shared blocks are shared by identity and written once.

## Keyframe codecs

| Codec | Key | Holds | Scale | Used for |
|---|---|---|---|---|
| 1 | -- | constant vector: the track's reference value | | positions, scales |
| 2 | -- | constant rotation: the reference (x, y, z; w derived) | | rotations |
| 3 | 16 | f32 x, y, z + u32 frames | | positions |
| 4 | 8 | u16 x, y, z + u16 frames | extremes | positions, scales |
| 5 | 4 | u8 x, y, z + u8 frames | extremes | positions, scales |
| 6 | 8 | 4 x 14-bit signed + 8-bit frames | x 4/16383 | rotations |
| 7 | 4 | 4 x 7-bit + 4-bit frames | extremes | rotations |
| 11/12/13 | 4 | one axis (X/Y/Z) + w, 14-bit + 4-bit frames | see below | rotations about one axis |
| 14 | 6 | 4 x 11-bit + 4-bit frames | extremes | rotations |
| 15 | 5 | 4 x 9-bit + 4-bit frames | extremes | rotations |

"Extremes" = value = offset + scale * component, component = n / (2^bits - 1). The 9- and 11-bit
codecs split some fields across 16-bit words / bytes (bit maps in `lmtcodec.py`).

**Codecs 11-13 differ between the games** -- the one real semantic difference:
- DDO: through extremes like the other bilinear codecs (all 119,809 tracks have extremes).
- DDDA: no extremes (all 91,924 tracks); the stored axis and w are signed like codec 6 and the two
  other axes come from the track's reference value. DDDA's loader points these tracks' extremes at their
  own reference (0x00E9D402). Proof: on the 8,959 keys whose reference is non-zero on the other axes,
  100% decode to unit quaternions with the reference axes, 55% with zeros.

Which codecs use extremes is also written into the loaders: DDO relocates the extremes pointer for codecs
4, 5, 7 and 11-15 only; DDDA for all but 11-13.

Proof of the codec bit layouts, beyond the byte-exact re-pack: rotation keys dequantise to unit
quaternions (DDDA: all but 1,802 of 4.5 M sampled keys within 0.05; DDO: all but 145 of 9.8 M -- the
rest are coarse 7/9-bit keys), and every buffer's frame deltas add up to its motion's frame count - 1.

## Moving motions between the games (`convert_lmt`)

What changes (everything else is copied bit for bit):
1. The version word: each loader accepts only its own (DDDA `cmp word [ecx+4], 0x42`, DDO `0x43`).
2. Codec 11-13 tracks are re-encoded as codec 6, which both games decode alike.
3. DDO's codec 6 tracks never carry extremes (its loader would not relocate them): dropped.
4. DDO's flag bit 0 is cleared for DDDA.

**Proof on every file** (`check_corpus --only lmt` runs it): DDO -> DDDA all 1,402 files (1,944,772
tracks, 119,809 re-encoded), DDDA -> DDO all 1,002 (905,046 tracks, 91,924 re-encoded); every track keeps
its joint id, usage, reference and key frames; the largest value change anywhere is 0.000122 (half a
codec 6 step); porting back reproduces the source version.

Commands:

```bat
Riftstone.cmd lmt info  motion/pl/m/m00/m0004_at/m0004_at.lmt          :: motions, tracks, codecs
Riftstone.cmd lmt keys  <lmt> --motion 0 --limit 4                     :: decoded keyframes per track
Riftstone.cmd lmt bones <lmt>                                          :: joint ids driven
Riftstone.cmd lmt convert <file.lmt> --to ddda [--drop-bones 55,150-154] -o out.lmt
Riftstone.cmd lmt convert obj/pl/pl000000/motion/m0001/m0001_at/m0001_at.lmt --game ddo --to ddda --retarget -o out.lmt
Riftstone.cmd port obj/pl/pl000000/motion/m0009/m0009_at/m0009_at.lmt --mod "My Mod" --as <ddda path>
Riftstone.cmd skeleton obj/pl/pl000000/model/pl000000_00.mod model/pl/m/m_base/m000/m000.mod --game ddo --vs-game ddda
```

## Do DDO's player animations fit DDDA's player? (`tools/motion_compat.py`)

Bodies: DDO `obj\pl\pl000000\model\pl000000_00` / `_01` (`rom/Human`, 132 joints, ids 0-223) and DDDA
`model\pl\m\m_base\m000\m000` / `f_base\f000\f000` (`rom/game_main`, 224 joints, ids 0-252). The two
share 127 joint ids.

DDO's 194 player motion lists drive 65 joints:
- **57 are the same joint in DDDA's body** (same parent, offset within 0.5 cm): 0-16, 18-20, 22-54,
  66-69 -- root, spine, neck, head, arms, hands, legs.
- **6 are rigged differently**: 150-154 (weapon attachment joints: DDO hangs them off the hands and the
  spine, DDDA off the hips) and 55 (DDO: a right-shoulder helper under joint 9; DDDA: a left upper-arm
  twist under joint 6).
- **17 and 21** (the toes: DDDA has them under the ankles; DDO's body does not, but its bow motions
  animate them).

Per vocation, the share of tracks on identical joints or the root: Fighter 86.9%, Seeker 87.2%,
Hunter 84.9%, Priest 84.2%, Shield Sage 81.6%, Sorcerer 84.0%, Warrior 80.9%, Element Archer 80.0%,
Alchemist 87.7%, Spirit Lancer 83.2%, High Scepter 83.8%, shared human set 76.1%. **No** track sits on a
joint whose rest offset differs; every other track is on the six re-rigged joints. Porting with
`--drop-bones 55,150-154` leaves only tracks on identical joints (the weapon then follows DDDA's own
attachment, UNKNOWN in game).

### Online places wrists and ankles by goals (`tools/limb_goals.py`)

The same joints do not always mean the same keys. Many of Online's player motions key no rotation for
the elbows and knees (7, 11, 15, 19) and key a *position* for the wrists and ankles (8, 12, 16, 20).
These positions are not offsets from the elbow or knee. For example, the Alchemist's Alma Wave
(`m0009_cs01` motion 0x64) moves the left wrist from (-51.1, 107.0, 31.7), while its rest offset is
(-27, 0, 0). They are goals in the model's own space: ankles about 10 cm above the ground, wrists about a
metre up.

`tools/limb_goals.py` reads every goal on Online's own body, in the 196 player motion lists (1,984 track
lists), every third frame, in two ways:

| Chain | Track lists with a goal | Within reach in model space | Bone's length as a local offset |
|---|---|---|---|
| left arm (6-7-8, 26 + 27 cm) | 1,326 | 99.51% | 0.04% |
| right arm (10-11-12) | 1,326 | 99.76% | 0.02% |
| left leg (14-15-16, 44 + 44 cm) | 1,669 | 100.00% | 2.59% |
| right leg (18-19-20) | 1,669 | 99.99% | 3.44% |

- "Within reach" is measured from the shoulder or hip where the motion places it (1 cm slack). The worst
  goals are 1.09 times the arm's reach and 1.01 times the leg's.
- So Online bends each elbow and knee itself to reach its goal (two-joint IK). Its solver, including which
  way a joint bends and whether it also turns the shoulder or hip, is UNKNOWN; DDO.exe's motion code is
  where to read it.
- 369 track lists key the elbows directly, and 2 key the knees.
- Dark Arisen's 110 player motion lists (956 track lists) key no position on any of these joints. They
  key the elbows' and wrists' rotations in 860 track lists, and the knees' and ankles' in 848.

A converted motion keeps the goals as position tracks. Read as local offsets, like every other position
track, they leave the elbows and knees straight and put a hand 31-183 cm from its elbow in Alma Wave (the
forearm is 27 cm). This is the likely cause of the compat layer's odd look (`docs/compat-layer.md`). It is
UNKNOWN in game. Fixing it means baking the IK into rotation tracks when converting.

### Do the hitboxes follow?

DDO's player hit shapes (`obj\pl\pl000000\collision\jobNN\...` `.ocl`, `src/riftstone/ocl_ddo.py`) attach
to joints through `mJnt0` / `mJnt1`. `tools/motion_compat.py` counts those references by what the joint
is in DDDA's body (2026-09-25, every player collision file):

| Vocation | Joint refs | On identical joints | On a weapon joint |
|---|---|---|---|
| Fighter (01) | 172 | 17 | 155 on 150 |
| Seeker (02) | 47 | 47 | - |
| Hunter (03) | 3 | 3 | - |
| Priest (04) | 2 | 2 | - |
| Shield Sage (05) | 100 | 99 | 1 on 151 |
| Warrior (07) | 246 | 11 | 235 on 152 |
| Element Archer (08) | 1 | 1 | - |
| Alchemist (09) | 1 | 1 | - |
| Spirit Lancer (10) | 165 | 2 | 163 on 152 |
| High Scepter (11) | 281 | 2 | 279 on 150 |
| shared | 60 | 60 | - |

Sorcerer (06) has no joint-attached hit shapes. The body joints used (0, 1, 2, 4, 8, 12, 13, 16, 20) have
the same parent and offset in both bodies, and the port keeps their tracks unchanged, so those shapes
keep their place on the body. The weapon joints are keyed by both games' motions (rotation and position
tracks in every player motion list; translations up to ~450 cm in DDDA, ~800 cm in DDO), but each game
keys them against its own parent: DDO 150 under hand 8, 151 under hand 12, 152 under spine joint 2; DDDA
150 under the hips (1), 151 and 152 under 150. Ported unchanged, a DDO weapon track is applied to the
wrong parent, and the weapon and the hit shapes on it land in the wrong place. Dropping those tracks
(`--drop-bones`) leaves the weapon unanimated.

### The rebake (`src/riftstone/retarget.py`, `--retarget`)

`riftstone lmt convert <lmt> --to ddda --retarget` does it on request. `riftstone port` and Studio's
Port do it by default for player motion lists (`obj/pl/pl000000/motion/...`, `motion/pl/...`;
`--no-retarget`, or `"retarget": false` in the API, ports as-is). It does three things:
- it computes, at every frame, the world transform the source body gives each re-parented joint the
  motions place;
- it re-expresses that transform under the joint's destination parent (parents first);
- it writes the joint's rotation and position as one key per frame: codec 6 and codec 3, both keyed
  without extremes in vanilla files of both games.

The bodies default to the two player bodies (`port.PLAYER_BODY`); `--src-body`, `--dst-body` and
`--retarget-joints` override them.

Joint 55 is left as it is. The motions key only its rotation, and the two bodies mean different joints
by it (DDO's right-shoulder helper at x = +17 cm, DDDA's left upper-arm twist at x = -24 cm).

**Conventions**, measured on the games' own data and checked in DDDA.exe's joint routine 0x01051B60:
- Matrices are row-major with points as row vectors, and world = local x parent's world. This fits
  every model with bones of both games: 1,832 DDDA and 2,938 DDO; the other order fails on 244 of 275.
- The local transform is scale, then rotation, then translation.
- A quaternion maps to the transpose of the usual matrix (the DirectX form).
- Tracks replace the rest value.

**Result** (`tools/retarget_check.py`, 2026-09-25): every DDO player motion list (194 lists, 2,119
motions, 341,653 joint-frames) played on DDDA's body after `convert_lmt`, against DDO's body playing the
original:

| | Weapon joints 150-154, position | Angle |
|---|---|---|
| With the rebake | max 0.000036 cm | max 0.027 deg (codec 6's step) |
| Without | max 1,322 cm, p99 275 cm | up to 180 deg |

| Hit-shape attach points | With: max / p99 (cm) | Without: max / p99 (cm) |
|---|---|---|
| Fighter (150) | 0.048 / 0.037 | 429.8 / 347.7 |
| Shield Sage (151) | 0.008 / 0.007 | 234.8 / 213.6 |
| Warrior (152) | 0.084 / 0.059 | 288.3 / 131.5 |
| Spirit Lancer (152) | 0.057 / 0.038 | 223.3 / 104.8 |
| High Scepter (150) | 0.127 / 0.083 | 911.9 / 893.5 |

Every rebaked file rebuilds byte-exact before and after conversion (194/194). The reverse direction
(110 DDDA lists on DDO's body) gives at most 0.000017 cm. The cost: DDO's player lists grow 78%, from
9.4 MB to 16.7 MB, because the rebaked joints are keyed on every frame.

UNKNOWN until played:
- **Interpolation.** How the engine interpolates rotations between keys: if slerp rather than the
  assumed nlerp, the error grows to 0.56 cm / 1.4 deg.
- **Between frames.** Whether the engine samples between frames. Between frames a rebaked joint moves
  in a straight line under its new parent, not along DDO's arc.
- **Scale.** Whether a parent's scale reaches its children. Joint 150 is scaled in 23 DDO motions and
  is DDDA's parent of 151/152.
- **Unkeyed joints.** What the engine does with joints a motion does not key.

## UNKNOWN (needs the game running)

- How a ported motion looks and plays in the other game: timing, root motion, weapon attachment.
- What event bits trigger (hit windows, sounds, effects) and whether the ids mean the same in both games.
- What the float tracks drive, and what DDO's flag bit 0 does.
- Hunter / Element Archer bow motions drive the toes (17, 21) that DDO's body lacks; in DDDA they would
  move its toes.

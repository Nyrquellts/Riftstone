# Archive tags: DDDA.exe's archive registry

Found 2026-09-24 from a pasted dump of this table (`list: tag path`, sorted as text), then read in full
from `DDDA.exe` (Steam build 2364871). Everything here is static: read from the exe's data and code and
from the game's archives. **In game: UNKNOWN** for every behaviour below.

## The table

The engine can load an archive by number. The numbers are values of `ARCHIVE_TAG` (the PS3 build
names `sArchiveManager::getArchivePath(ARCHIVE_TAG)`, `sArchiveManager::loadRecArcTag`,
`cTagArcLoad::setArcTag`).

- 10 lists. `.data 0x01823544` holds 10 × `{u32 records; u32 count}`; `.rdata 0x0153B6BC` points at each.
- A record is 8 bytes, `{u32 tag; const char *path}`. The path is relative to `nativePC`, without `.arc`.
- 13,061 records, tags 1..33,927, each tag once. 20,866 values in that range are unused. `0x848A`
  (33,930) means "no tag".
- Tags are per build: the PS3 build numbers them differently (`om0000` is 130 there, 188 on PC).

| # | list (PS3 name) | tags | tag range | files that ship | contents |
|---|---|---|---|---|---|
| 0 | `eLetc` | 1,325 | 1..25,192 | 287 | `bbs_rpg`, `title`, `game_main`, `Initialize` (1-4); the 65 stage archives (5-69); the 97 enemies (71-182); the 63 shells (18,742-18,804); stage100 `area`/`fm` (24,096-24,137); `etc`, `bbsrpg_core`, `prologuePack`; five `dl1` archives (24,148-24,152); the DLC stage slots (24,153-25,192) |
| 1 | `eLgSt100ArchiveList` | 5,290 | 18,806..24,095 | 1,328 | overworld cells (below) |
| 2 | `eLgOmArcList` | 759 | 188..946 | 724 | objects |
| 3 | `eLgNpcArcList` | 2,345 | 948..3,293 | 1,228 | `npc`, `npcfca`, `npcfca_jp`, `mnpc`, `h_enemy` |
| 4 | `eLgWpnArcList` | 1,447 | 3,295..18,721 | 965 | `wp`, `pwnmsg`, `npcfsm`, `voice` |
| 5 | `eLgEquipArcList` | 1,152 | 4,922..16,825 | 1,152 | `eq` (18 of them in `dl1`) |
| 6 | `eLgSkillArcList` | 179 | 16,827..17,005 | 179 | `sk` |
| 7 | `eLgEvtArcList` | 397 | 25,193..33,673 | 377 | `quest`, `event` |
| 8 | `eLgGuiArcList` | 60 | 33,739..33,798 | 59 | `gui` |
| 9 | `eLgStNaviMeshArcList` | 107 | 33,800..33,927 | 107 | per-stage navigation meshes |

Read it yourself (stdlib, exe read-only). Sorting the printed lines as text reproduces the pasted dump
exactly (checked on all 13,061 lines):

```python
import struct
exe = open(r"C:\Program Files (x86)\Steam\steamapps\common\DDDA\DDDA.exe", "rb").read()
pe = struct.unpack_from("<I", exe, 0x3C)[0]
nsec, opt = struct.unpack_from("<H", exe, pe + 6)[0], struct.unpack_from("<H", exe, pe + 20)[0]
secs = [struct.unpack_from("<IIII", exe, pe + 24 + opt + 40 * i + 8) for i in range(nsec)]  # vsize, va, rsize, raw
def off(va):
    rva = va - 0x400000
    return next(raw + rva - sva for vs, sva, rs, raw in secs if sva <= rva < sva + rs)
def cstr(va):
    o = off(va)
    return exe[o:exe.index(b"\0", o)].decode()
for lst in range(10):
    recs, count = struct.unpack_from("<II", exe, off(0x01823544 + 8 * lst))
    for k in range(count):
        tag, path = struct.unpack_from("<II", exe, off(recs + 8 * k))
        print(f"{lst}: {tag} {cstr(path)}")
```

## How the engine uses tags

- `0x00419100` (at start) stores `&record` at `0x018D9288 + 4 × tag` for all 10 lists (33,930 slots).
- `0x004191D0` resolves a tag through that array and reports a tag that has no record.
- `0x00419140` turns a path into its tag, searching only the object list and `eLetc`; `0x848A` when absent.
- Loaders queue tags, not paths: `0x00418550` gives a request slot, `0x00418810` adds a tag to it,
  `0x00418610` starts it (roles read from how the call sites use them).
- The stage loader queues the stage archive as tag `5 + stage index` (`0x004FFDA7`), `em5800` (The Dragon,
  tag 162) on stages 501/502 and `em5801` (The Ur-Dragon, tag 163) on stage 605.
- Stage index order (tag − 5; the DLC slots use the same order): 100 200 210 220 230 240 250 300 310 320
  330 370 380 500 501 502 600-610 700-706 611 615 800-803 400 401 402 405 406 410 411 413 420 421 423 424
  425 430 431 435 436 440 443 444 445 446 447 450 804.

The function names in this page are descriptions, not engine symbols.

## Anything loaded by tag must be in the table

- 6,406 of the 8,536 shipped archives have a tag. The other 2,130 load by name: `map` (1,579), `item_b`
  icons (401, 13 of them in `dl1`), `sa/DX9` (76), `ingamemanual` (56) and 18 `eq`.
- The table is fixed in `.rdata`, and the tag array is filled only from it. So an archive the engine loads
  by tag has to be one of these paths. All 97 vanilla enemies have tags, so any of them can load in any
  stage; a new `emNNNN` (a DDO port, say) has none. Giving it one needs a plugin: a record under an
  unused tag written into `0x018D9288` after `0x00419100` runs, plus whatever maps an enemy to its tag
  (not traced).

## DLC stage slots: dead

- `rom\dlN\stage\stageSSS\stageSSS_set` for N = 1..16 and all 65 stages: 1,040 tags,
  `24,153 + 65 × (N − 1) + stage index` (24,153..25,192). Two ship: `dl1` 443 and 444.
- The stage loader walks N = 1..16 (`0x004FFDC9`..`0x004FFE57`), computes each tag (`0x004FFDE2`),
  formats a path it never uses, and queues the archive only when N = 1 and the stage is 443 or 444
  (`0x004FFE16`..`0x004FFE2C`). The other 1,038 are never requested, so an archive at one of those paths
  does nothing without a patch there.
- Stage 443 is the post-game Everfall (its random sets are groups 0-19; see `AGENTS.md`). 444: not identified.

## DLC group lists: the engine's own add-on lists

- The `.gpl` loader (`0x00CC6140`: magic `gpl`, version `0x9E`, 295 group slots) reads `mDLCNo` after
  `mGroupList` and `mSetBit`. When it is 1..15 it sets bit `mDLCNo` in one global mask at
  `[[0x018FA504] + 0x15190]` (`0x00CC62AA`). The only other writer found is its owner's constructor,
  which zeroes it (`0x0049FC33`).
- Three group-list loaders, `_e` (`0x004AA330`), `_n` (`0x004AA470`) and `_p` (`0x004AA8E0`), read
  `scr\stSSS\etc\stSSS<t>`, then for every set bit NN (0..15) also `scr\stSSS\etc\stSSS<t>_dlcNN`
  (format string at `0x015623B4`). `_t` lists have no such loop.
- Vanilla: `st443_e_dlc01` and `st444_e_dlc01` (in the `dl1` set archives) are the only group lists with
  `mDLCNo` = 1, and their groups carry `mDLCNoBits` = 1. The other 197 group-list copies have `mDLCNo` = 0.
- **Lead** for separate encounter mods on one stage (the group-list clash in `AGENTS.md`, the roadmap's
  "merge group lists" item): a mod adds groups by shipping its own `stSSS_e_dlcNN` (`mDLCNo` = NN, its
  groups' `mDLCNoBits` = NN) instead of replacing `stSSS_e`, so two mods for one stage do not overwrite
  each other's list. Open before building it:
  - the list must be parsed (setting its bit) before the stage's lists are read; shipping it inside the
    stage archive may do that (not checked);
  - NN is 1..15 per stage and list type (vanilla uses 1 on 443/444). The mask is global, so a set bit
    makes every stage look for its own `_dlcNN`; vanilla already does that with bit 1;
  - group numbers stay one space (0..294) across all of a stage's lists, so mods still need distinct numbers;
  - how saves record these groups: not checked;
  - in game: UNKNOWN.

  The dead `_set` slots are not needed for this.

## Overworld cells (list 1)

- Stage 100 is a 40 × 33 grid (X, the number before `n`, 30..69; Z, the number before `m`, 40..72, as the
  engine's split data at `0x0153023C` names them). Each cell has one tag per kind,
  `base + 33 × (X − 30) + (Z − 40)`, with bases split 18,806, split_way 20,126, split_sub 21,446 and
  lot 22,776. `splitfmfore\f00`..`f09` are 22,766..22,775.
- The terrain kinds load only where a static bitmap in `.data` has the cell's bit (1,320 bits, most
  significant first): split `0x018228E8`, split_way `0x01822990`, split_sub `0x01822A38`. They equal the
  shipped cells exactly (418, 366, 333 cells). The streamer skips a clear bit (`0x005006B0`, `0x005007C0`,
  `0x00500900`), so a new terrain cell needs its bit set by a plugin.
- `lot` has no bitmap: `0x004A19A0` (stage 100 only) turns a cell pair into the tag and queues it. Its
  caller `0x004A18F0` walks a 10-entry request list whose owner is not identified. 201 cells ship a `lot`
  archive; 1,119 have a tag and no file. Whether play ever requests one of those: UNKNOWN.

## Tagged but not shipped

- quests `q0033`-`q0038`, `q0056`, `q0069`, `q0070`, `q0072`, `q0077`, `q0082`-`q0085`, `q0122`-`q0124`,
  `q0126`, `q0127`
- 35 objects; 176 `npc` and 214 `npcfca`; all 727 `npcfca_jp`
- 394 weapon variants (`_c`/`_d` and similar); 88 pawn-message archives (`pwnmsg\stage` 80, `pwnmsg\em` 8)
- `gui\GUIuGUICmcMessage`
- 3,962 overworld cell archives and 1,038 DLC stage slots (above)

## Not supported by the measurements

A chat summary of this work (2026-09-25) claimed the following. The exe says otherwise:

- "Every asset is registered with an immutable ID": 2,130 shipped archives have no tag, and tags differ
  between builds.
- "`0x004191D0` is what the engine calls to mount any file": several loaders read the tag array
  directly, and untagged archives load by name.
- "The DLC slots are IDs 24,148-25,076": they are 24,153-25,192. 24,148-24,152 are five shipped `dl1`
  archives, and 25,076 is where the paste was cut off.
- "Patch the check and `dl2`..`dl16` load as overlays, so mod conflicts are gone": loading another archive
  adds its resources; it does not merge them. A resource with the same name as the base one still leaves
  one copy in use (which one: UNKNOWN). The engine's conflict-free mechanism is the separate `_dlcNN`
  group list above: `_e`/`_n`/`_p` lists only, group numbers still shared, in game UNKNOWN.
- "The shell table tells which shell an enemy needs": it maps tags to paths only. Enemy-to-shell
  dependencies come from archive references (`riftstone world deps`).
- Names: `em0601` is Snow Harpies (Goblins are `em0100`), `em5100` is Golems (The Dragon is `em5800`),
  and stage 443 is the post-game Everfall, not Bitterblack Isle.

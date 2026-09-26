# The spawn system, reversed from the PS3 build

Why this exists: placing a custom encounter (N of enemy X in room Y) must be a **known
quantity**, not a guess. This is the enemy-spawn pipeline traced from the PS3 build. Addresses are PS3; the PC exe is mapped via vtable
order (see `docs/re-enemy-cap.md`). Each claim is from the disassembly; UNKNOWNs are marked.

## The pipeline (enemy path)

```
aStage::init / sSave::toGameStageInit
  └─ sSetManager::createSet(int)                         PS3 0x00103B74
       ├─ cLotMgr<cLayoutSetEnemy>::setupGroupParam      (the enemy path; the .gpl groups)
       ├─ cLotMgr<cLayoutSetNpc>::setupGroupParam        (the separate ~30-cap pawn path)
       ├─ cLotMgr<cLayoutSetOm>::setupGroupParam         (objects)
       ├─ createSplitEnemySet(...)                       PS3 0x0010356C
       └─ changeUnitNumEnemy(uint)                       PS3 0x00102948  (the 10-slot pool)
  cLayoutSetEnemy::setLayoutUnit(rLayout*, ...)          PS3 0x0046A374  (reads the .lot)
       └─ per matching placement: sSetManager::registerEmData(...)   PS3 0x001056F4
  cLayoutSetEnemy::setEnemyAll(bool)                     PS3 0x0046ABD0  (creates the units)
       └─ per empty slot: vtable createUnit → sEnemyManager::createUnit  PS3 0x0004CB80
```

## What decides how many spawn (measured)

- **`setEnemyAll`** (0x0046ABD0) walks the set's slot array — count at `this+0x0C`, entries via
  `this+0x18` — and for every **empty** slot calls the virtual `createUnit`, storing the unit back
  into the slot. So **spawned count = the set's slot count**, one unit per slot.
- **The per-group count is `.gpl`-driven.** `loadSetCountToGroupParam<cLayoutSetEnemy>` (0x0116FECC)
  iterates the **295-slot** group array (`cArray<cOmGroupData,295>`), skips groups whose low bit
  isn't set (inactive) or whose type field isn't **5** (the enemy-set type), matches each `.gpl`
  group to the layout record by **group id** (record `+0x1C >> 23`), and packs that group's count
  from `cOmGroupData+8` into the record at **`+0x128`**. So the spawn count is a **per-group** number
  that comes from the `.gpl` group (`mSetCountMax`), not the raw placement count.
- **`.lot` placements supply positions for a group.** `setLayoutUnit` (0x0046A374) iterates the
  layout, switches on the record **kind** (0–6 switch at 0x46A6C0), checks the group with
  `cLayoutSetCharaBase::isManageGroup` (0x00467DFC), and calls **`registerEmData`** (0x001056F4) per
  enemy. The group a placement belongs to is what ties it to the count above.
- **`mSetCountMax` = -1 is special-cased.** `setLayoutUnit` compares the group count against `-1`,
  `0`, `1` (0x46A628–0x46A638); `-1` takes the "no fixed per-group cap" path (spawn from the group's
  placements). `>= 0` caps the group at that many.
- **`setEnemyAll`** (0x0046ABD0) then creates one unit per empty slot in the set's slot array
  (count at `this+0x0C`), which is sized from the per-group counts above.
- **The hard ceiling is the pool.** `changeUnitNumEnemy` bounds the active enemy slots to **10**
  (28-byte entries; `docs/re-enemy-cap.md`). So however many groups ask for, **at most ~10 enemies
  are on screen** until that native cap is raised.

**The game's own hordes settle the count model (measured on the corpus, `docs/world-map.md`).** Every
capped enemy group but one uses respawn type 5, and the caps are totals, not simultaneous counts:
stage 330's group 35 sends **100** goblins and hobgoblins from **7** placements, stage 706's group 3
(the Proving Grounds) **50** goblins from **6**; groups with a cap below their placements (stage 100's
group 104: 4 of 30) pick among them. So:

- **N of an enemy, a few at a time:** one group, `mSetCountMax = N`, respawn type 5, and as many
  placements as should be on screen at once (the placements are spawn points it refills).
- **N at once:** `mSetCountMax = -1` and N placements -- visible result min(N, ~10) until the enemy pool
  is raised natively.

`riftstone encounter` writes exactly the first form (`docs/world-map.md`, "100 goblins").

**Model preloading, partly answered:** `cLayoutSetEnemy::addArcLoadTbl` (PS3 0x0046932C, called from
`cLayoutSetEnemy::move`) starts the tagged archive load for a unit. It takes a
`cLayoutSetCharaBase::cUnitData`, the layout set's own unit entry (an earlier note said
`cGroupParam::cUnitData`; the PS3 mangled name says otherwise). That the enemy in that entry comes
from the group's unit list is the working assumption behind `riftstone encounter`, not a traced
fact (`docs/world-map.md`). In game for an enemy new to a stage: UNKNOWN until played.

**Which files, measured:** a stage's groups are `st<S>_{e,n,p,t}.gpl`; group N's placements are the
layouts `st<S>_<X>m<Z>n_<t>N` (the engine builds that name, 0x01562268); group numbers are slots
0..294 of the 295-entry table.

## Still being traced (do not ship as fact yet)

- **The exact count packing:** the bit-packing of the count at `cOmGroupData+8` → record `+0x128`
  (the `rldicl` extractions in `loadSetCountToGroupParam`), and how `setEnemyAll` sizes its slot array
  from it. The rule above comes from the game's data; this would confirm it from the code. Anchors:
  `loadSetCountToGroupParam` 0x0116FECC, `registerEmData` 0x001056F4, `changeUnitNumEnemy` 0x00102948.
- **Respawn types 2, 3, 4, 6**: in use (5, 29, 19 and 4 enemy groups), behaviour UNKNOWN.
- **Per-room fit**: a huge enemy in a small room (physics/nav) — a runtime property, will need an
  in-game check.

## Rooms are solved

`rStagePlaceName` (`.spn`) is decoded byte-exact (`flat.py`), giving the definitive
**stage → room-name** map (`docs/stage-map.md`, `tools/gen_stage_map.py`): e.g. `st210` = The
Encampment, `st400` = The Warriors' Respite (BBI hub), `st706` = Proving Grounds. No more guessing
which stage is which room.

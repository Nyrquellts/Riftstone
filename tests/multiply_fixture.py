"""The stand-in game ``riftstone multiply`` is tested and fuzzed on: tests/world_fixture.py's game with ordinary
placements (no AI script of their own, as nearly all of the game's are) and two more stages.

Stage 424: world_fixture's groups (goblins, a capped set of hobgoblins, a chimera, a cell with two different
numbers, the corridor's navigation mesh and the five goblins on it, the DLC list's group).  Stage 425: a layout
no group list names.  Stage 426: a group of 20 goblins in two layouts (its ids run out), a set that picks 3 of
its 5, and a group of nothing to copy (a scripted goblin, the Dragon).  Stage 100: one cell of the open field with
its walkable collision, two goblins on it and a harpy 8 m over it.
"""
from pathlib import Path
from unittest import mock

import helpers
import world_fixture
from riftstone import arc, lot, terrain, typemap

LOT, GPL, SBC = typemap.BY_EXT["lot"], typemap.BY_EXT["gpl"], typemap.BY_EXT["sbc"]
FIELD = terrain.Cell(45, 55)


def enemy(kind: int, name: str, rid: int, pos) -> lot.Record:
    """world_fixture.enemy with no AI script of its own (the game's ordinary placement); the chimera is a big
    monster, as the game flags every one of its own."""
    r = lot.blank(kind, rid, mName=name, mPosition=pos, mOrder=4, mSetID=-1, mLifePointGroup=0xFFFFFFFF,
                  mEmItemFlag=-1, mEmItemTable=-1, mAreaHitNo=-1)
    if name == "em5200":
        r.fields["mBossFlag"] = 1
    return r


def group(stage: int, number: int, units, cells=((0, 0),), **kw) -> dict:
    g = world_fixture.group(number, units, **kw)
    g["mLayoutIDArray"] = [{"mLayoutID": stage, "mGroup": number, "mSplitX": x, "mSplitZ": z} for x, z in cells]
    return g


def field_height(x: float, z: float) -> float:
    ground = terrain.Ground(lambda name: helpers.cell_collision())
    return ground.under((x, 5100.0, z))[0]


def extra_stages(game) -> None:
    """Stages 426 and 100 (the module's docstring), written into the stand-in game."""
    rom = game.root / "nativePC" / "rom" / "stage"
    many_a = lot.Lot([enemy(4, "em0100", i, (300.0 * i, 0.0, 0.0)) for i in range(12)])
    many_b = lot.Lot([enemy(4, "em0100", 12 + i, (300.0 * i, 0.0, 10500.0)) for i in range(8)])
    picked = lot.Lot([enemy(5, "em0101", i, (300.0 * i, 0.0, 3000.0)) for i in range(5)])
    quest = enemy(4, "em0100", 0, (0.0, 0.0, 6000.0))
    quest.fields["mFsmFilePath"] = b"AI\\FSM\\Enemy\\st426\\quest\\q0001_target"
    story = lot.Lot([quest, enemy(32, "em5800", 1, (900.0, 0.0, 6000.0))])
    lists = world_fixture.group_list([group(426, 0, ["em0100"], ((0, 0), (0, 1))),
                                      group(426, 1, ["em0101"], count_max=3, respawn=5),
                                      group(426, 2, ["em0100", "em5800"])])
    (rom / "stage400" / "stage426.arc").write_bytes(arc.Archive([
        arc.Entry.from_data(b"scr\\st426\\etc\\st426_e", GPL, lists),
        arc.Entry.from_data(lot.layout_name(426, 0, 0, "e", 0).encode(), LOT, lot.build(many_a)),
        arc.Entry.from_data(lot.layout_name(426, 1, 0, "e", 0).encode(), LOT, lot.build(many_b)),
        arc.Entry.from_data(lot.layout_name(426, 0, 0, "e", 1).encode(), LOT, lot.build(picked)),
        arc.Entry.from_data(lot.layout_name(426, 0, 0, "e", 2).encode(), LOT, lot.build(story))]).build())
    ox, _, oz = FIELD.offset
    spots = [(ox + 3000.0, oz + 3000.0), (ox + 3400.0, oz + 3000.0)]
    walkers = [enemy(4, "em0100", i, (x, field_height(x, z), z)) for i, (x, z) in enumerate(spots)]
    flyer = enemy(19, "em0600", 2, (ox + 6000.0, field_height(ox + 6000.0, oz + 6000.0) + 800.0, oz + 6000.0))
    (rom / "stage100").mkdir(parents=True)
    (rom / "stage100" / "stage100.arc").write_bytes(arc.Archive([
        arc.Entry.from_data(b"scr\\st100\\etc\\st100_e", GPL, world_fixture.group_list(
            [group(100, 143, ["em0100", "em0600"], ((FIELD.n, FIELD.m),))])),
        arc.Entry.from_data(lot.layout_name(100, FIELD.m, FIELD.n, "e", 143).encode(), LOT,
                            lot.build(lot.Lot(walkers + [flyer]))),
        arc.Entry.from_data(FIELD.collisions[1].encode(), SBC, helpers.cell_collision())]).build())


def make(root: Path):
    """The stand-in game under ``root``."""
    with mock.patch.object(world_fixture, "enemy", enemy):
        game = world_fixture.make(root, extras=True, cells=True, navmesh=True, bare=True)
    extra_stages(game)
    return game

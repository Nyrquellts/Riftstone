"""A stand-in game with one stage laid out the way the real ones are, for the world map and encounter
tests (and the encounter fuzz target): group lists, the layouts their groups own, a cell's statics, a
place list, the enemy name table and an enemy archive."""
from __future__ import annotations

from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import arc, flat, gmd, gpl, lot, typemap
from riftstone.game import Game

import test_gpl

STAGE = 424


def group(number: int, units, count_max=-1, respawn=1, appear=(0, 0)) -> dict:
    g = test_gpl.group()
    g.update(mGroupClass=0, mGroup=number, mPriority=10,
             mUnitKindList=[{"name": u, "isBelong": 1} for u in units],
             mLayoutIDArray=[{"mLayoutID": STAGE, "mGroup": number, "mSplitX": 0, "mSplitZ": 0}])
    g["mSetCountMax"] = count_max
    g["mRspnCondition.mRspnType"] = respawn
    g["mAppearBgn"], g["mAppearEnd"] = appear
    return g


def group_list(groups) -> bytes:
    marks = [0] * 295
    for g in groups:
        marks[g["mGroup"]] = 0x80000000
    return gpl.build(gpl.Gpl(158, marks, [0] * 16, 0, groups))


def enemy(kind: int, name: str, rid: int, pos) -> lot.Record:
    return lot.blank(kind, rid, mName=name, mPosition=pos, mOrder=4, mSetID=-1, mFsmFilePath=f"ai\\{name}.fsm",
                     mLifePointGroup=0xFFFFFFFF, mEmItemFlag=-1, mEmItemTable=-1, mAreaHitNo=-1)


def layouts() -> dict[str, bytes]:
    s = STAGE
    goblins = lot.Lot([enemy(4, "em0100", i, (100.0 * i, -350.0, -8800.0)) for i in range(3)])
    hobs = lot.Lot([enemy(5, "em0101", i, (5000.0, -350.0, 2000.0 + 50 * i)) for i in range(2)])
    chest = lot.blank(48, 0, mName="om1030", mPosition=(10.0, 0.0, 10.0), mSetTableID=5, mSetItemNo=-1,
                      mSetTableIDNight=-1, mSetItemNoNight=-1)
    statics = lot.Lot([lot.blank(46, 0, mName="om0511", mPosition=(1.0, 2.0, 3.0))])
    return {lot.layout_name(s, 0, 0, "e", 0): lot.build(goblins),
            lot.layout_name(s, 0, 0, "e", 3): lot.build(hobs),
            lot.layout_name(s, 0, 0, "p", 0): lot.build(lot.Lot([chest])),
            lot.layout_name(s, 0, 0, "s", 0): lot.build(statics)}


def names(lang: int = 1) -> bytes:
    msgs = [gmd.Message("Goblins", "e0100_goblin"), gmd.Message("Hobgoblins", "e0101_hobgoblin"),
            gmd.Message("Greater Goblins", "em0103_"), gmd.Message("Harpies", "e0600_harpy")]
    return gmd.build(gmd.Gmd(lang, "TextWeb", msgs))


def places(lang: int = 1) -> bytes:
    return gmd.build(gmd.Gmd(lang, "TextWeb", [gmd.Message("Hall of Tests"), gmd.Message("Crypt")]))


def spn() -> bytes:
    rec = {"mPosX": 0.0, "mPosY": 0.0, "mPosZ": 0.0, "mRadius": 100.0, "mPlaceNameId": 0, "mUnk26": 0,
           "mUnk28": 0, "mUnk2C": 0.0, "mUnk30": 0.0}
    return flat.build(flat.Flat("spn", flat.SCHEMAS["spn"][0], {"mConst": 0x30, "mpPlace": [rec, dict(rec, mPlaceNameId=1)]}))


CHIMERA_TEXTURES = ("e5200_skin_BM", "e5200_face_BM", "e5200_hebi_BM", "e5200_eye_BM")
CHIMERA_MATERIALS = {"e5200_a": ["e5200_hebi_NM", "e5200_hebi_BM", "e5200_face_BM", "e5200_skin_BM", "e5200_skin_CMM",
                                 "e5200_eye_BM", "e5200_eye01_BM"],
                     "e5200_00_a": ["e5200_hebi_NM", "e5200_hebi_BM", "e5200_furdm_BM"],
                     "e5200_01_a": ["e5200_hebi_NM", "e5200_hebi_BM", "e5200_hebi_CMM"]}


def chimera_texture(seed: int = 0) -> bytes:
    """A 16x16 DXT5 (format 24) texture with three mips, as the chimera's albedo maps are."""
    import struct
    from riftstone import tex
    mips, offs, off, pixels = 3, [], 16 + 12, bytearray()
    for m in range(mips):
        sz = tex._mip_size(16, 16, m, 24)
        offs.append(off)
        off += sz
        pixels += bytes((i * 11 + m + seed) & 0xFF for i in range(sz))
    body = struct.pack("<3I", *offs) + bytes(pixels)
    return tex.build(tex.Tex(0x20000, tex.VERSION, mips, 16, 16, 1, 24, 1, body))


def chimera_material(textures) -> bytes:
    from riftstone import mrl
    texs = [mrl.Texture(mrl.TEX_TYPE_ID, 0, 0, (f"model\\em\\e52\\e5200\\{n}".encode() + b"\0").ljust(mrl.NAME_LEN, b"\xcd"))
            for n in textures]
    mats = [mrl.Material(0x1CAB245E, 0x6FC86CDB, list(range(13)))]
    return mrl.build(mrl.Mrl(mrl.VERSION, 0xB46006D5, texs, mats, b"\xcd\xcd\xcd\xcd"))


def field_goblins() -> lot.Lot:
    """Group 6's goblins in cell 05m02n: three ordinary ones (two with swords, one with a bow), one running
    a quest's own AI script and one in a life point group, as stage 100's field goblins are set up."""
    recs = [enemy(4, "em0100", i, (20000.0 + 100 * i, 300.0, 50000.0)) for i in range(5)]
    for r, equip in zip(recs, (0, 0, 1, 0, 2)):
        r.fields["mFsmFilePath"] = b""
        r.fields["mEquipType"] = equip
    recs[3].fields["mFsmFilePath"] = b"AI\\FSM\\Enemy\\st424\\quest\\q0007_target"
    recs[4].fields["mLifePointGroup"] = 48          # its health is kept with life point group 48
    return lot.Lot(recs)


def start_positions(points) -> bytes:
    """rStartPos (``.stp``): one rStartPos::Info per door, as the game's files hold them."""
    from riftstone import xfs
    import helpers

    info = xfs.ClassDef(typemap.jamcrc("rStartPos::Info"), 0x70, (
        helpers.prop("Pos", "vector3"), helpers.prop("Ang", "f32"), helpers.prop("OfsPos", "vector3", 0x20),
        helpers.prop("OfsAng", "f32", 0x20)))
    array = xfs.ClassDef(typemap.jamcrc("MtArray"), 0x10, (helpers.prop("mAutoDelete", "bool"),
                                                          helpers.prop("mpArray", "classref", 0x20)))
    root = xfs.ClassDef(typemap.jamcrc("rStartPos"), 0x20, (helpers.prop("mQuality", "u32"),
                                                           helpers.prop("InfoList", "class")))
    infos = [xfs.Obj(2, [[(float(p[0]), float(p[1]), float(p[2]), 0.0)], [0.0], [(100.0, 0.0, 0.0, 0.0)] * 3,
                         [0.0, 0.0, 0.0]]) for p in points]
    return xfs.build(xfs.Xfs(1, [root, array, info], xfs.Obj(0, [[2], [xfs.Obj(1, [[0], infos])]])))


NAV_DOORS = ((-800.0, -350.0, -9800.0), (8500.0, 650.0, 8500.0))   # the corridor's west end; the island


def navmesh_extras() -> tuple[dict, list]:
    """What ``navmesh=True`` adds to stage 424's archive: group 7, five goblins standing on the corridor's floor,
    and group 8, five harpies 8 m over it (so the goblins walk and the harpies do not, as the game's own do)."""
    walkers = lot.Lot([enemy(4, "em0100", i, (2500.0 + 300 * i, -350.0, -9500.0)) for i in range(5)])
    flyers = lot.Lot([enemy(19, "em0600", i, (1000.0 + 300 * i, 450.0, -9000.0)) for i in range(5)])
    g7 = group(7, ["em0100"])           # loads only under lot flag 100, as every group of stages 420-447 does
    g7["mLoadCondition.mLotFlag"], g7["mDataLotFlag.mFlagNo"] = 1, 100
    return ({lot.layout_name(STAGE, 0, 0, "e", 7): lot.build(walkers),
             lot.layout_name(STAGE, 0, 0, "e", 8): lot.build(flyers)},
            [g7, group(8, ["em0600"])])


def make(root: Path, extras: bool = False, cells: bool = False, navmesh: bool = False,
         bare: bool = False) -> Game:
    """Write the stand-in game under root and return it.  ``extras`` adds what the skin tests need: a
    chimera group with a placement and the chimera's archive (materials and albedo maps), and a DLC
    enemy group list whose group 1 takes the base list's first free number (as st443_e_dlc01 does).
    ``cells`` adds group 6, whose layout sits in a cell with two different numbers: the file is named
    05m02n and the group lists the cell as mSplitX 2, mSplitZ 5 (as every such layout in the game is).
    ``navmesh`` adds the navigation mesh stage 424 loads (stage 420's, as in the game: a corridor with a hole
    and a raised island, tests/nav_fixture.py), the stage's two doors (``.stp``) and navmesh_extras().
    ``bare`` adds stage 425: a goblin layout for its group 0, and no enemy group list in the game."""
    rom = root / "nativePC" / "rom"
    (rom / "stage" / "stage400").mkdir(parents=True)
    (rom / "gui").mkdir(parents=True)
    (rom / "enemy").mkdir(parents=True)
    (root / "DDDA.exe").write_bytes(b"stub")
    LOT, GPL = typemap.BY_EXT["lot"], typemap.BY_EXT["gpl"]
    s = STAGE
    base_groups = [group(0, ["em0100"]), group(3, ["em0101", "em0100"], 40, 5)]
    if extras:
        base_groups.append(group(4, ["em5200"]))
    if cells:
        g6 = group(6, ["em0100"])
        g6["mLayoutIDArray"] = [{"mLayoutID": s, "mGroup": 6, "mSplitX": 2, "mSplitZ": 5}]
        base_groups.append(g6)
    nav_layouts = {}
    if navmesh:
        nav_layouts, nav_groups = navmesh_extras()
        base_groups += nav_groups
    entries = [arc.Entry.from_data(f"scr\\st{s}\\etc\\st{s}_e".encode(), GPL, group_list(base_groups)),
               arc.Entry.from_data(f"scr\\st{s}\\etc\\st{s}_p".encode(), GPL, group_list([group(0, [])])),
               arc.Entry.from_data(f"scr\\st{s}\\etc\\st{s}".encode(), typemap.BY_EXT["spn"], spn())]
    entries += [arc.Entry.from_data(n.encode(), LOT, d) for n, d in layouts().items()]
    entries += [arc.Entry.from_data(n.encode(), LOT, d) for n, d in nav_layouts.items()]
    if navmesh:
        import nav_fixture
        from riftstone import nav

        (rom / "stage" / "stage400" / "stage420_nav.arc").write_bytes(arc.Archive([arc.Entry.from_data(
            b"scr\\st420\\etc\\st420_nav", typemap.BY_EXT["nav"], nav.build(nav_fixture.corridor_with_island()))]).build())
        (rom / "game_main.arc").write_bytes(arc.Archive([arc.Entry.from_data(
            f"scr\\st{s}\\etc\\st{s}".encode(), typemap.BY_EXT["stp"], start_positions(NAV_DOORS))]).build())
    if cells:
        entries.append(arc.Entry.from_data(lot.layout_name(s, 5, 2, "e", 6).encode(), LOT, lot.build(field_goblins())))
    if extras:
        chimera = lot.Lot([enemy(27, "em5200", 0, (0.0, -350.0, 0.0))])
        entries.append(arc.Entry.from_data(lot.layout_name(s, 0, 0, "e", 4).encode(), LOT, lot.build(chimera)))
        dl1 = rom / "dl1" / "stage" / f"stage{s}"
        dl1.mkdir(parents=True)
        dlc_layout = lot.Lot([enemy(4, "em0100", 0, (300.0, -350.0, -8800.0))])
        (dl1 / f"stage{s}_set.arc").write_bytes(arc.Archive([
            arc.Entry.from_data(f"scr\\st{s}\\etc\\st{s}_e_dlc01".encode(), GPL, group_list([group(1, ["em0100"])])),
            arc.Entry.from_data(lot.layout_name(s, 0, 0, "e", 1).encode(), LOT, lot.build(dlc_layout))]).build())
        TEX, MRL = typemap.BY_EXT["tex"], typemap.BY_EXT["mrl"]
        em = [arc.Entry.from_data(f"model\\em\\e52\\e5200\\{n}".encode(), TEX, chimera_texture(i))
              for i, n in enumerate(CHIMERA_TEXTURES)]
        em += [arc.Entry.from_data(f"model\\em\\e52\\e5200\\{n}".encode(), MRL, chimera_material(ts))
               for n, ts in CHIMERA_MATERIALS.items()]
        (rom / "enemy" / "em5200.arc").write_bytes(arc.Archive(em).build())
    (rom / "stage" / "stage400" / f"stage{s}.arc").write_bytes(arc.Archive(entries).build())
    if bare:
        goblin = lot.Lot([enemy(4, "em0100", 0, (0.0, -350.0, -8800.0))])
        (rom / "stage" / "stage400" / "stage425.arc").write_bytes(arc.Archive([
            arc.Entry.from_data(lot.layout_name(425, 0, 0, "e", 0).encode(), LOT, lot.build(goblin))]).build())
    g = typemap.BY_EXT["gmd"]
    (rom / "gui" / "names.arc").write_bytes(arc.Archive([
        arc.Entry.from_data(b"id\\DDN\\message\\common\\enemy_name_eng", g, names()),
        arc.Entry.from_data(b"id\\DDN\\message\\common\\map_placelist_eng", g, places())]).build())
    from riftstone import arcref
    (rom / "shell").mkdir(parents=True)
    shell = [(b"effect\\efl\\em\\e0100_arrow", typemap.BY_EXT["efl"]), (b"model\\sh\\arrow", typemap.BY_EXT["mod"])]
    (rom / "shell" / "shellem0100.arc").write_bytes(arc.Archive([arc.Entry.from_data(n, t, b"x") for n, t in shell]).build())
    (rom / "enemy" / "em0100.arc").write_bytes(arc.Archive([
        arc.Entry.from_data(b"model\\em\\e01\\e0100", typemap.BY_EXT["tex"], b"TEX\0" + bytes(16)),
        arc.Entry.from_data(b"rom\\shell\\shellem0100", typemap.BY_EXT["arc"], arcref.build(arcref.for_names(shell)))]).build())
    return Game(root)

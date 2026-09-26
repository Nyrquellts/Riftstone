"""Stand-in games for the monster tests (and the monster fuzz target's seeds): a Dark Arisen and an Online
install with a few enemy archives each, laid out the way the real ones are -- rigged models whose
envelopes name the joints their meshes are bound to, materials, patterned albedo maps, motion lists and
the name tables both games name their enemies with.

  DDDA  em0200 Wolves (e0200)       em2000 Skeletons (e0300: the archive id is not the model's)
        em5200 Chimeras (e5200 body + e5200_00 goat + e5200_01 snake parts, the full-detail e5200_a ...)
  DDO   EM010200 Wolf               EM010203 Warg (em010200's model_org + its own material)
        EM010204 Grimwarg (the wolf rig, longer bones, the wolf's texture layout)
        EM010221 Skeleton Warg (the wolf rig, longer bones, another texture layout)
        EM010300 Skeleton           EM015200 Chimera (one model: body, goat and snake joints)
        EM015202 White Chimera      EM011000 Rogue Fighter (no enemy body in its archive)
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import arc, gmd, lmt, mrl, port, tex, texcodec, typemap
from riftstone.game import Game

MOD, MRL, TEX, LMT, GMD = (typemap.BY_EXT[e] for e in ("mod", "mrl", "tex", "lmt", "gmd"))
FMT = 0xD8297028          # a vertex format both games use
DDO_ONLY_FMT = 0xB392101F  # one only Online's models use
ALBEDO = mrl._jam20("tAlbedoMap")

# (joint id, parent joint id or None, offset): a small four-legged rig
WOLF = [(0, None, (0.0, 60.0, 0.0)), (1, 0, (0.0, 0.0, 0.0)), (2, 1, (0.0, 5.0, 30.0)), (3, 2, (0.0, 10.0, 20.0)),
        (4, 1, (-10.0, -20.0, 25.0)), (5, 4, (0.0, -25.0, 0.0)), (6, 1, (10.0, -20.0, 25.0)),
        (7, 6, (0.0, -25.0, 0.0)), (8, 1, (0.0, 0.0, -40.0)), (9, 8, (0.0, 0.0, -20.0))]
SKELETON = [(0, None, (0.0, 90.0, 0.0)), (1, 0, (0.0, 0.0, 0.0)), (2, 1, (0.0, 20.0, 0.0)), (3, 2, (0.0, 30.0, 0.0)),
            (4, 3, (0.0, 20.0, 0.0)), (5, 2, (-20.0, 25.0, 0.0)), (6, 2, (20.0, 25.0, 0.0))]
CHIMERA = [(0, None, (0.0, 230.0, 0.0)), (1, 0, (0.0, 0.0, 0.0)), (2, 1, (0.0, 0.0, 47.0)), (3, 2, (0.0, 0.0, 60.0)),
           (4, 3, (-20.0, 13.0, 51.0)), (5, 3, (20.0, 13.0, 51.0))]
GOAT = [(10, 2, (0.0, 26.0, 21.0)), (11, 10, (0.0, 33.0, 35.0))]
SNAKE = [(20, 1, (0.0, 0.0, -70.0)), (21, 20, (0.0, 0.0, -60.0))]


def longer(joints, by: float = 1.5):
    """The same rig with every offset scaled: same joints and parents, other proportions."""
    return [(j, p, tuple(v * by for v in off)) for j, p, off in joints]


def model(joints, bound, version: int = 0xD4, materials=(b"body",), fmts=(FMT,)) -> bytes:
    """A rigged model in the shared section layout (port.model_info): `joints` [(id, parent id, offset)],
    one mesh per vertex format, each with envelopes naming the `bound` joints (by bone index)."""
    nb, nm, nmat = len(joints), len(fmts), len(materials)
    index = {j: i for i, (j, _, _) in enumerate(joints)}
    bones = 0x84
    mats = bones + nb * (24 + 64 + 64) + 0x100 if nb else 0x84
    meshes = mats + nmat * 0x80
    envs = [index[j] for j in bound]
    nenv = len(envs) * nm
    vb = meshes + nm * port.MOD_MESH.size + nenv * 144
    stride, nv = 24, 3
    vbs = stride * nv
    ib = vb + vbs
    ni = 3
    end = ib + 2 * ni
    end += -end % 4
    out = bytearray(end)
    head = port.MOD_HEADER.pack(b"MOD\0", version, nb, nm, nmat, nv, ni, 0, vbs, 0, 0, bones if nb else 0, 0, mats,
                                meshes, vb, ib, end)
    out[:len(head)] = head
    struct.pack_into("<I", out, 0x80, nenv)
    for i, (j, parent, off) in enumerate(joints):
        p = 255 if parent is None else index[parent]
        struct.pack_into("<BBBBff3f", out, bones + 24 * i, j, p, 255, 0, 0.0, sum(v * v for v in off) ** 0.5, *off)
    remap = bones + nb * (24 + 64 + 64)
    out[remap:remap + 0x100] = b"\xff" * 0x100
    for i, (j, _, _) in enumerate(joints):
        out[remap + j] = i
    for k, name in enumerate(materials):
        out[mats + 0x80 * k:mats + 0x80 * k + len(name)] = name
    for k, fmt in enumerate(fmts):
        out[meshes + 48 * k:meshes + 48 * (k + 1)] = port.MOD_MESH.pack(
            0, nv, k % nmat, (stride << 16) | 0x1000000, 0, 0, fmt, 0, ni, 0, 0, len(envs), k, 0, nv - 1, 0)
    e0 = meshes + nm * port.MOD_MESH.size
    for k in range(nenv):
        struct.pack_into("<I", out, e0 + 144 * k, envs[k % len(envs)])
    struct.pack_into("<3H", out, ib, 0, 1, 2)
    return bytes(out)


def material(version: int, textures: list[str], names=(b"body",)) -> bytes:
    """One material per name, each binding every texture in turn to the albedo slot (JAMCRC(name) keys)."""
    texs = []
    for p in textures:
        t = mrl.Texture(mrl.TEX_TYPE_ID, 0, 0, b"")
        t.set_name(p)
        texs.append(t)
    recs, parts = [], []
    for n in names:
        binds = [(ALBEDO, i + 1) for i in range(len(textures))][:1] + [(mrl._jam20(f"tMap{i}"), i + 1)
                                                                        for i in range(1, len(textures))]
        cmd = b"".join(mrl.CMD.pack(mrl.SET_TEXTURE | (0xDCDC << 4), idx, slot << 12) for slot, idx in binds)
        recs.append(mrl.Material(0x1CAB245E if version == 0x20 else 0x7A116358, zlib.crc32(n) ^ 0xFFFFFFFF,
                                 [len(cmd), 0, 0, 0, len(binds), 0, 0, 0, 0, 0, 0, 0, 0]))
        parts.append((cmd, b""))
    return mrl.assemble(version, 0xB46006D5, texs, recs, parts)


def pattern(kind: str, side: int = 64) -> bytes:
    """RGBA pixels with structure a mirror breaks: 'a' a ramp with a notch, 'b' diagonal bands."""
    px = bytearray()
    for y in range(side):
        for x in range(side):
            if kind == "a":
                v = (x * 255 // side) if y < side // 2 or x < side // 4 else 40
                px += bytes((v, 255 - v, (y * 4) & 0xFF, 255))
            else:
                v = 250 if ((x + 2 * y) // 8) % 2 else 10
                px += bytes((v, v, 255 - v, 255))
    return bytes(px)


def sheet(kind: str, version: int, side: int = 64) -> bytes:
    """A BC3 albedo map (format 24) of the given game's revision with a pattern."""
    attr1 = tex.attr1_for(0x20000, version)
    return texcodec.encode(side, side, pattern(kind, side), tex.Tex(attr1, version, 1, side, side, 1, 24, 1, b""))


def motions(version: int, joints) -> bytes:
    """A motion list with one motion whose tracks drive these joint ids (and the root track)."""
    ref, one = struct.pack("<4f", 0, 0, 0, 1), struct.pack("<f", 1.0)
    tracks = lmt.TrackList([lmt.Track(2, 0, 0, j, one, ref) for j in joints] + [lmt.Track(1, 1, 0, 255, one, ref)])
    m = lmt.Motion(tracks, 2, -1, bytes(16), ref, 0x800000, [lmt.EventGroup(bytes(64)) for _ in range(4)], None)
    return lmt.build(lmt.Lmt(version, [m]))


def _arc(path: Path, entries, encrypted: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(arc.Archive([arc.Entry.from_data(n.encode(), t, d, encrypted=encrypted) for n, t, d in entries],
                                 encrypted=encrypted).build())


def _family(folder: str, name: str, joints, bound, version: int, mrl_version: int, pattern_kind: str,
            fmts=(FMT,), motion_folder: str | None = None, lmt_version: int = 66) -> list:
    """A body's model, material, albedo map and motion list (DDDA or DDO naming)."""
    tex_name = f"{folder}\\{name}_body_BM"
    motion = f"{motion_folder or folder}\\{name}_co\\{name}_co"
    return [(tex_name, TEX, sheet(pattern_kind, tex.VERSION if version == 0xD4 else tex.VERSION_DDO)),
            (f"{folder}\\{name}", MRL, material(mrl_version, [tex_name])),
            (f"{folder}\\{name}", MOD, model(joints, bound, version, fmts=fmts)),
            (motion, LMT, motions(lmt_version, [j for j, _, _ in joints]))]


def make_ddda(root: Path) -> Game:
    rom = root / "nativePC" / "rom"
    rom.mkdir(parents=True)
    (root / "DDDA.exe").write_bytes(b"stub")
    wolf = [j for j, _, _ in WOLF if j]
    wolves = _family("model\\em\\e02\\e0200", "e0200", WOLF, wolf, 0xD4, 0x20, "a", motion_folder="motion\\em\\e02")
    detail = "model\\em\\e02\\e0200\\e0200_body_NM"          # the full-detail material binds one map more
    wolves.insert(0, (detail, TEX, sheet("a", tex.VERSION)))
    wolves.append(("model\\em\\e02\\e0200\\e0200_a", MRL, material(0x20, ["model\\em\\e02\\e0200\\e0200_body_BM", detail])))
    _arc(rom / "enemy" / "em0200.arc", wolves, False)
    _arc(rom / "enemy" / "em2000.arc", _family("model\\em\\e03\\e0300", "e0300", SKELETON, [1, 2, 3, 4, 5, 6], 0xD4,
                                                0x20, "b", motion_folder="motion\\em\\e03"), False)
    folder = "model\\em\\e52\\e5200"
    body = [j for j, _, _ in CHIMERA if j]
    chim = _family(folder, "e5200", CHIMERA, body, 0xD4, 0x20, "a", motion_folder="motion\\em\\e52")
    maps = ["e5200_skin_BM", "e5200_face_BM", "e5200_hebi_BM", "e5200_eye_BM"]
    chim += [(f"{folder}\\{m}", TEX, sheet("a", tex.VERSION, 16)) for m in maps]
    hebi = [f"{folder}\\e5200_hebi_NM", f"{folder}\\e5200_hebi_BM"]
    chim += [(f"{folder}\\e5200_a", MRL, material(0x20, [f"{folder}\\{m}" for m in maps])),
             (f"{folder}\\e5200_00", MOD, model(CHIMERA[:3] + GOAT, [10, 11], 0xD4, materials=(b"goat",))),
             (f"{folder}\\e5200_00", MRL, material(0x20, hebi[1:], names=(b"goat",))),
             (f"{folder}\\e5200_00_a", MRL, material(0x20, hebi, names=(b"goat",))),
             (f"{folder}\\e5200_01", MOD, model(CHIMERA[:2] + SNAKE, [20, 21], 0xD4, materials=(b"snake",))),
             (f"{folder}\\e5200_01", MRL, material(0x20, hebi[1:], names=(b"snake",))),
             (f"{folder}\\e5200_01_a", MRL, material(0x20, hebi, names=(b"snake",)))]
    _arc(rom / "enemy" / "em5200.arc", chim, False)
    names = [gmd.Message("Wolves", "e0200_wolf"), gmd.Message("Skeletons", "e0300_skeleton"),
             gmd.Message("Chimeras", "e5200_chimera")]
    _arc(rom / "gui" / "names.arc", [("id\\DDN\\message\\common\\enemy_name_eng", GMD,
                                     gmd.build(gmd.Gmd(1, "TextWeb", names)))], False)
    return Game(root, "ddda")


def make_ddo(root: Path) -> Game:
    rom = root / "nativePC" / "rom"
    rom.mkdir(parents=True)
    (root / "DDO.exe").write_bytes(b"stub")
    wolf = [j for j, _, _ in WOLF if j]
    base = "obj\\em\\em010200\\model"
    em = rom / "EM"
    _arc(em / "EM010200.arc", _family(base, "em010200", WOLF, wolf, 0xD2, 0x22, "a",
                                      motion_folder="obj\\em\\em010200\\motion", lmt_version=67), True)
    warg = [e for e in _family("obj\\em\\em010200\\model_org", "em010200", WOLF, wolf, 0xD2, 0x22, "a",
                               motion_folder="obj\\em\\em010200\\motion", lmt_version=67)]
    own = "obj\\em\\em010203\\model\\em010203_body_BM"
    warg += [(own, TEX, sheet("a", tex.VERSION_DDO)),
             ("obj\\em\\em010203\\model\\em010203", MRL, material(0x22, [own]))]
    _arc(em / "EM010203.arc", warg, True)
    _arc(em / "EM010204.arc", _family("obj\\em\\em010204\\model", "em010204", longer(WOLF), wolf, 0xD2, 0x22, "a",
                                      motion_folder="obj\\em\\em010204\\motion", lmt_version=67), True)
    _arc(em / "EM010221.arc", _family("obj\\em\\em010221\\model", "em010221", longer(WOLF), wolf, 0xD2, 0x22, "b",
                                      motion_folder="obj\\em\\em010221\\motion", lmt_version=67), True)
    _arc(em / "EM010300.arc", _family("obj\\em\\em010300\\model", "em010300", SKELETON, [1, 2, 3, 4, 5, 6], 0xD2,
                                      0x22, "b", fmts=(FMT, DDO_ONLY_FMT), motion_folder="obj\\em\\em010300\\motion",
                                      lmt_version=67), True)
    merged = CHIMERA + GOAT + SNAKE
    chim_folder = "obj\\em\\em015200\\model"
    maps = ["skin_BM", "face_BM", "hebi_BM", "eye_BM"]
    chim = _family(chim_folder, "em015200", merged, [j for j, _, _ in merged if j], 0xD2, 0x22, "a",
                   motion_folder="obj\\em\\em015200\\motion", lmt_version=67)
    chim += [(f"{chim_folder}\\em015200_{m}", TEX, sheet("a", tex.VERSION_DDO, 16)) for m in maps]
    _arc(em / "EM015200.arc", chim, True)
    white = [(n.replace("\\model\\", "\\model_org\\") if t == MOD or n.endswith("\\em015200") else n, t, d)
             for n, t, d in chim]
    white += [(f"obj\\em\\em015202\\model\\em015202_{m}", TEX, sheet("b", tex.VERSION_DDO, 16)) for m in maps]
    white += [("obj\\em\\em015202\\model\\em015202", MRL,
               material(0x22, [f"obj\\em\\em015202\\model\\em015202_{m}" for m in maps]))]
    _arc(em / "EM015202.arc", white, True)
    _arc(em / "EM011000.arc", [("obj\\wp\\wp000000\\model\\wp000000", MOD, model([], [], 0xD2))], True)
    ids = {1: [0x010200], 2: [0x010203], 3: [0x010204], 4: [0x010221], 5: [0x010300], 6: [0x015200],
           7: [0x015202], 8: [0x011000]}
    text = ["Wolf", "Warg", "Grimwarg", "Skeleton Warg", "Skeleton", "Chimera", "White Chimera", "Rogue Fighter"]
    msgs = [gmd.Message(t, f"ENEMY_NAME_{i + 1}") for i, t in enumerate(text)]
    emg = struct.pack("<II", 1, len(ids)) + b"".join(struct.pack("<3I", n, 0, len(v)) + struct.pack(f"<{len(v)}I", *v)
                                                    for n, v in ids.items())
    _arc(rom / "ui" / "names.arc", [("ui\\00_message\\enemy\\enemy_name", GMD,
                                    gmd.build(gmd.Gmd(0, "TextWeb", msgs, version=gmd.VERSION_DDO))),
                                   ("param\\enemy_group", typemap.type_for_extension("emg"), emg)], True)
    return Game(root, "ddo")


def make(base: Path) -> dict:
    return {"ddda": make_ddda(base / "ddda"), "ddo": make_ddo(base / "ddo")}

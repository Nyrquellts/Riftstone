"""Bring resources from one Dragon's Dogma to the other: Dark Arisen (``ddda``) <-> Online (``ddo``).

Both games run MT Framework 2 with the same containers; what differs is measured, not assumed
(``check_corpus`` and the surveys in docs/ddo-bridge.md):

  tex   same header and pixel layout; revision 0x099 <-> 0x09D, and DDO sets attr1 bit 1
        (0x20000 <-> 0x20002).  Formats both games use port as they are.
  gmd   1.2.1 <-> 1.3.2 through the parsed messages (DDO derives its label hash tables).
  mod   one section map for all 3,712 DDDA and 7,711 DDO models (header, bones, groups, material
        names, meshes, envelopes, vertex and index buffers); only the revision differs (212 vs 210).
        A mesh whose vertex format the other game never uses is refused, not guessed at.
  mrl   the games use different material classes (DDO: nDraw::DDMrlStdEst*, DDDA:
        nDraw::DDMaterialStd ...), so a material cannot be copied across.  It is rebuilt from a
        template of the destination game (normally the material of the model being replaced):
        one material per ported model material (hash = JAMCRC(name)), each copied from the template
        material whose shader texture slots best match, with every texture binding re-pointed to the
        source material's texture in the same slot (tAlbedoMap, tNormalMap, tSpecularMap ...).
  lmt   same record layouts (lmt.py); version 66 <-> 67, single-axis rotation tracks re-encoded
        (the games decode codecs 11-13 differently), see convert_lmt.
  sbc   one layout for all 1,496 DDDA and 4,042 DDO collision meshes (sbc.py); only the version word
        differs (0x77DF2114 vs 0x77DF43D8).  Surface attributes are copied as they are.

Everything a port writes is structurally checked here; how it looks in game stays UNKNOWN until
someone plays it.
"""

from __future__ import annotations

import copy
import math
import struct
import zlib
from dataclasses import dataclass, field

from . import gmd, mrl, sbc, tex, typemap
from .errors import RiftError

# Vertex formats each game's models use (every mesh of every model, measured).  A format outside
# the destination's set has no shader input layout there that Riftstone can vouch for.
VERTEX_FORMATS = {
    "ddda": frozenset((0x0cb68015, 0x14d40020, 0x207d6037, 0x49b4f029, 0x5e7f202c, 0x747d1031,
                       0x77d87022, 0x926fd02e, 0x9399c033, 0xa14e003c, 0xa320c016, 0xa7d7d036,
                       0xa8fab018, 0xafa6302d, 0xb0983013, 0xb6681034, 0xb86de02a, 0xbb424024,
                       0xc31f201c, 0xc66fa03a, 0xd8297028, 0xd84e3026, 0xda55a021, 0xdb7da014)),
    "ddo": frozenset((0x0cb68015, 0x14d40020, 0x207d6037, 0x49b4f029, 0x5e7f202c, 0x64593023,
                      0x667b1019, 0x747d1031, 0x926fd02e, 0x9399c033, 0xa14e003c, 0xa320c016,
                      0xa7d7d036, 0xa8fab018, 0xafa6302d, 0xb0983013, 0xb392101f, 0xbb424024,
                      0xc31f201c, 0xcbf6c01a, 0xd8297028, 0xd877801b, 0xdb7da014)),
}
# Texture format ids each game's textures use.
TEX_FORMATS = {"ddda": frozenset((19, 20, 24, 25, 31, 37, 39, 40, 43, 47)),
               "ddo": frozenset((10, 14, 19, 20, 22, 24, 25, 31, 37, 39, 40, 43))}
TEX_VERSION = {"ddda": tex.VERSION, "ddo": tex.VERSION_DDO}
GMD_VERSION = {"ddda": gmd.VERSION, "ddo": gmd.VERSION_DDO}
MOD_VERSION = {"ddda": 0xD4, "ddo": 0xD2}
MRL_VERSION = {"ddda": 0x20, "ddo": 0x22}
MATERIAL_CLASSES = {  # material class ids (JAMCRC & 0x7FFFFFFF of the class name) seen in each game
    0x0854D484: "nDraw::MaterialNull", 0x5FB0EBE4: "nDraw::MaterialStd",
    0x1CAB245E: "nDraw::DDMaterialStd", 0x26D9BA5C: "nDraw::DDMaterialInner",
    0x30DBA54F: "nDraw::DDMaterialWater", 0x7D2B31B3: "nDraw::MaterialStdEst",
    0x7A116358: "nDraw::DDMrlStdEstScr", 0x655A905B: "nDraw::DDMrlStdEstObj",
    0x17EBEBB4: "nDraw::DDMrlStdEstScrVtxAlpBld", 0x6178BCE8: "nDraw::DDMrlStdEstVfx",
    0x2E97D054: "nDraw::DDMrlStdEstScrWater",
}

MOD_HEADER = struct.Struct("<4sHHHHIII" + "I" * 10)
MOD_MESH = struct.Struct("<HHIIIIIIIIBBHHHI")
_EXT = {typemap.type_for_extension(e): e for e in ("tex", "gmd", "mod", "mrl", "lmt", "sbc")}


@dataclass
class Ported:
    data: bytes
    notes: list[str] = field(default_factory=list)


def ext_of(type_id: int) -> str:
    return typemap.extension(type_id)


def portable(type_id: int) -> bool:
    return type_id in _EXT


def _check_kinds(src: str, dst: str) -> None:
    if src not in MOD_VERSION or dst not in MOD_VERSION:
        raise RiftError(f"unknown game {src if src not in MOD_VERSION else dst!r} (ddda or ddo)")


# -- textures ------------------------------------------------------------------------------------
def convert_tex(data: bytes, src: str, dst: str, like: bytes | None = None) -> Ported:
    t = tex.parse(data)
    notes = []
    if t.fmt not in TEX_FORMATS[dst]:
        notes.append(f"format {t.fmt} is not used by any {dst.upper()} texture; whether that game "
                     "draws it is UNKNOWN")
    t.version = TEX_VERSION[dst]
    t.attr1 = tex.attr1_for(t.attr1, t.version)
    if like is not None:  # replacing a texture: keep that texture's attribute bits when the shape agrees
        old = tex.parse(like)
        if old.version == t.version and (old.attr1 & 0xF0000) == (t.attr1 & 0xF0000):
            t.attr1 = old.attr1
    return Ported(tex.build(t), notes)


# -- text ----------------------------------------------------------------------------------------
def convert_gmd(data: bytes, src: str, dst: str, like: bytes | None = None) -> Ported:
    g = gmd.parse(data)
    g.version = GMD_VERSION[dst]
    notes = []
    if like is not None:  # replacing a text file: it keeps that file's language code and name
        old = gmd.parse(like)
        if old.language != g.language:
            notes.append(f"language code {g.language_name} -> {old.language_name} (the file it replaces)")
        g.language, g.name = old.language, old.name
    if dst == "ddo":
        # DDO's key i names message i, so labels cover a leading run: unlabelled messages before the
        # last label get a placeholder key (every real label stays on its message)
        g.label_base = 0
        last = max((i for i, m in enumerate(g.messages) if m.label is not None), default=-1)
        taken = {m.label for m in g.messages if m.label is not None}
        filled = 0
        for i, m in enumerate(g.messages[:last + 1]):
            if m.label is None:
                label = f"RIFTSTONE_MSG_{i}"
                while label in taken:
                    label += "_"
                m.label = label
                taken.add(label)
                filled += 1
        if filled:
            notes.append(f"{filled} unlabelled message(s) before the last label got a placeholder key "
                         "(RIFTSTONE_MSG_<id>): DDO names message i by key i")
    out = gmd.build(g)
    gmd.parse(out)  # the destination parser must accept it (DDO: hash tables re-derived and checked)
    return Ported(out, notes)


# -- models --------------------------------------------------------------------------------------
@dataclass
class ModelInfo:
    version: int
    material_names: list[bytes]
    vertex_formats: list[int]
    bones: int
    joints: list[int] = field(default_factory=list)   # each bone's joint number (what animations address)


def model_info(data: bytes) -> ModelInfo:
    if len(data) < 0x84 or data[:4] != b"MOD\0":
        raise RiftError("not an MT Framework model (MOD\\0)")
    h = MOD_HEADER.unpack_from(data)
    (_, ver, nb, nm, nmat, _nv, ni, _ne, vbs, _ntex, ng, bones, groups, mats, meshes, vb, ib, end) = h
    nenv = struct.unpack_from("<I", data, 0x80)[0]
    after_bones = bones + nb * (24 + 64 + 64) + 0x100 if nb else 0x84
    layout = [
        ((not nb) or bones == 0x84, "bones"),
        ((not ng) or groups == after_bones, "groups"),
        (mats == (groups + ng * 32 if ng else after_bones), "material names"),
        (meshes == mats + nmat * 0x80, "meshes"),
        (vb == meshes + nm * MOD_MESH.size + nenv * 144, "vertex buffer"),
        (ib == vb + vbs, "index buffer"),
        (end in (ib + ni * 2, ib + ni * 2 + ((-(ib + ni * 2)) % 4)) and len(data) >= end, "end"),
    ]
    bad = [what for ok, what in layout if not ok]
    if bad:
        raise RiftError(f"model sections are not where the shared layout puts them ({', '.join(bad)}); "
                        "not porting a model Riftstone cannot vouch for")
    names = [data[mats + i * 0x80:mats + (i + 1) * 0x80].split(b"\0", 1)[0] for i in range(nmat)]
    fmts = [MOD_MESH.unpack_from(data, meshes + i * MOD_MESH.size)[6] for i in range(nm)]
    joints = [data[bones + 24 * i] for i in range(nb)] if nb else []
    return ModelInfo(ver, names, fmts, nb, joints)


def convert_mod(data: bytes, src: str, dst: str, like: bytes | None = None) -> Ported:
    info = model_info(data)
    if info.version != MOD_VERSION[src]:
        raise RiftError(f"model revision {info.version} is not {src.upper()}'s ({MOD_VERSION[src]})")
    foreign = sorted({f for f in info.vertex_formats if f not in VERTEX_FORMATS[dst]})
    if foreign:
        n = sum(f in foreign for f in info.vertex_formats)
        raise RiftError(f"{n} mesh(es) use vertex format(s) {', '.join(f'0x{f:08x}' for f in foreign)} that "
                        f"no {dst.upper()} model uses; their shader input is not known to exist there")
    out = bytearray(data)
    struct.pack_into("<H", out, 4, MOD_VERSION[dst])
    notes = [f"{len(info.vertex_formats)} mesh(es), {len(info.material_names)} material name(s), "
             f"{info.bones} bone(s); vertex formats all used by {dst.upper()}"]
    if like is not None:   # replacing a model: the animations drive the replaced model's joints
        try:
            old = model_info(like)
        except RiftError:
            old = None
        if old is not None:
            extra = sorted(set(info.joints) - set(old.joints))
            if extra:
                notes.append(f"joints {extra[:12]}{' ...' if len(extra) > 12 else ''} are not in the model it "
                             "replaces; what moves them in game is UNKNOWN")
            else:
                notes.append(f"all {len(set(info.joints))} joints are ones the replaced model has")
    return Ported(bytes(out), notes)


# -- materials -----------------------------------------------------------------------------------
def retarget(template: bytes, dst: str | None = None, source: bytes | None = None,
             material_names: list[bytes] | None = None, material_hashes: list[int] | None = None,
             rename=None) -> Ported:
    """A destination-game material file rebuilt from `template` (a .mrl of that game).

    One material per key -- JAMCRC of each `material_names` entry (the ported model's), else
    `material_hashes`, else the `source` file's own -- each copied from the template material whose
    texture slots best match the source material of that key.  Every texture binding of the copy is
    re-pointed to the source material's texture in the same shader slot (tAlbedoMap, tNormalMap ...),
    passed through `rename` (e.g. the namespaced path the ported texture was written to; None keeps the
    template's texture); slots the source does not fill keep the template's texture too.
    """
    tpl = mrl.parse(template)
    if dst is not None and tpl.version != MRL_VERSION[dst]:
        raise RiftError(f"the template is a revision 0x{tpl.version:x} material, not {dst.upper()}'s")
    if not tpl.materials:
        raise RiftError("the template has no materials to copy")
    rename = rename or (lambda p: p)
    tpl_parts = mrl.blocks(template, tpl)
    tpl_slots = [{b.slot: b for b in mrl.bindings(template, x) if b.kind == mrl.SET_TEXTURE}
                 for x in tpl.materials]
    src_slots: dict[int, dict[int, str]] = {}
    src_order: list[int] = []
    if source is not None:
        s = mrl.parse(source)
        for x in s.materials:
            src_order.append(x.material_hash)
            src_slots[x.material_hash] = {b.slot: s.textures[b.value - 1].name for b in mrl.bindings(source, x)
                                          if b.kind == mrl.SET_TEXTURE and 0 < b.value <= len(s.textures)}
    if material_names is not None:
        keys = [zlib.crc32(n) ^ 0xFFFFFFFF for n in material_names]
    elif material_hashes is not None:
        keys = list(material_hashes)
    else:
        keys = src_order
    if not keys:
        raise RiftError("no materials to build: give the model's material names or a source material file")
    textures: list[mrl.Texture] = []
    where: dict[str, int] = {}

    def add(path: str, like: mrl.Texture) -> int:
        if path not in where:
            t = mrl.Texture(like.type_id, like.a, like.b, b"")
            t.set_name(path)
            textures.append(t)
            where[path] = len(textures)
        return where[path]

    recs, parts, notes = [], [], []
    moved = kept = 0
    for i, key in enumerate(keys):
        want = src_slots.get(key, {})
        # the template material sharing most texture slots with the source material (ties: same index)
        j = max(range(len(tpl.materials)),
                key=lambda k: (len(set(want) & set(tpl_slots[k])), k == min(i, len(tpl.materials) - 1)))
        cmd = bytearray(tpl_parts[j][0])
        for n, b in enumerate(mrl.bindings(template, tpl.materials[j])):
            if b.kind != mrl.SET_TEXTURE or not 0 < b.value <= len(tpl.textures):
                continue
            like = tpl.textures[b.value - 1]
            path = rename(want[b.slot]) if b.slot in want else None
            if path is None:  # the source fills no such slot, or its texture cannot come across
                path = like.name
                kept += 1
            else:
                moved += 1
            struct.pack_into("<I", cmd, n * mrl.CMD.size + 4, add(path, like))
        rec = copy.deepcopy(tpl.materials[j])
        rec.material_hash = key
        recs.append(rec)
        parts.append((bytes(cmd), tpl_parts[j][1]))
    out = mrl.assemble(tpl.version, tpl.type_hash, textures, recs, parts)
    check = mrl.parse(out)
    for x in check.materials:
        for b in mrl.bindings(out, x):
            if b.kind == mrl.SET_TEXTURE and b.value > len(check.textures):
                raise RiftError("rebuilt material binds a texture past its table")  # never expected
    shown = ([n.decode("latin-1") for n in material_names[:3]] if material_names is not None
             else [f"0x{k:08x}" for k in keys[:3]])
    notes.append(f"{len(recs)} material(s) ({', '.join(shown)}{' ...' if len(keys) > 3 else ''}) from "
                 f"{len(tpl.materials)} template material(s); {moved} texture binding(s) re-pointed, "
                 f"{kept} kept from the template")
    return Ported(out, notes)


def used_textures(data: bytes) -> list[str]:
    """Texture paths a material file actually binds."""
    m = mrl.parse(data)
    used = {b.value for x in m.materials for b in mrl.bindings(data, x) if b.kind == mrl.SET_TEXTURE and b.value}
    return [m.textures[i - 1].name for i in sorted(used) if i <= len(m.textures)]


def convert_mrl(data: bytes, src: str, dst: str, like: bytes | None = None) -> Ported:
    m = mrl.parse(data)
    classes = sorted({MATERIAL_CLASSES.get(x.shader, f"0x{x.shader:08x}") for x in m.materials})
    raise RiftError(f"materials do not port directly ({', '.join(classes)} are {src.upper()} material classes). "
                    f"Give a {dst.upper()} template with --like <material.mrl> (usually the material of the "
                    "model you replace); it is rebuilt with one material per source material and each texture "
                    "slot re-pointed to the source's texture for that slot")


# -- motions -------------------------------------------------------------------------------------
LMT_VERSION = {"ddda": 66, "ddo": 67}   # each game's loader accepts only its own (DDDA 0x00E9D389, DDO 0x015A4BD3)
_DDO_MOTION_BIT = 0x1                  # 19,597 of 19,685 DDO motions set it, no DDDA motion; no loader reads it


def _q14(v: float) -> int:
    """A quaternion component in codec 6's signed 14-bit form (* 16383 / 4, negatives as 16383 + n)."""
    if not math.isfinite(v):
        raise RiftError("a rotation key decodes to a value that is not a number (damaged extremes or reference)")
    n = max(-8191, min(8191, round(v * 16383 / 4)))
    return n if n >= 0 else 16383 + n


def parse_bones(text: str) -> set[int]:
    """'55,150-154' -> {55, 150, 151, 152, 153, 154} (joint ids 0..255)."""
    out: set[int] = set()
    for part in filter(None, (p.strip() for p in (text or "").split(","))):
        lo, dash, hi = part.partition("-")
        try:
            if dash and not hi:
                raise ValueError(part)
            a, b = int(lo), int(hi or lo)
        except ValueError:
            raise RiftError(f"bad joint id or range {part!r} (e.g. 55,150-154)") from None
        if not 0 <= a <= b <= 255:
            raise RiftError(f"joint ids run 0..255 (got {part!r})")
        out.update(range(a, b + 1))
    return out


# The player bodies whose skeletons a player motion list is rebaked between (the female bodies have
# the same chains for the joints concerned).
PLAYER_BODY = {"ddo": "obj/pl/pl000000/model/pl000000_00.mod", "ddda": "model/pl/m/m_base/m000/m000.mod"}
PLAYER_MOTIONS = ("obj/pl/pl000000/motion/", "motion/pl/")        # DDO's and DDDA's player motion folders


def is_player_motion(path: str) -> bool:
    """A player motion list (the kind whose weapon joints the rebake is for), by its engine path."""
    p = path.replace("\\", "/").lower().lstrip("/")
    return p.endswith(".lmt") and p.startswith(PLAYER_MOTIONS)


def convert_lmt(data: bytes, src: str, dst: str, like: bytes | None = None,
                drop_bones: set[int] | None = None, rebake: tuple | None = None) -> Ported:
    """A motion list for the other game. The layouts are the same (lmt.py); what differs, measured on
    both games' files and loaders:

    - the version word (66 / 67): each loader refuses the other's;
    - codecs 11-13 (single-axis rotations): DDDA stores them signed with the two other axes taken from
      the track's reference, DDO through extremes. Each such track is re-encoded as codec 6 (all four
      axes, signed 14-bit), which both games decode alike; the largest component change is reported;
    - codec 6 tracks carry no extremes in DDO (its loader would not relocate them): dropped (codec 6
      never reads them);
    - flag bit 0, set on DDO motions only: cleared for DDDA.

    Bone ids are kept: they are the skeleton's joint ids, and whether the destination skeleton has the
    same joints is a model question (``skeleton_diff``; ``tools/motion_compat.py`` measured the player
    bodies: 57 of the 65 joints DDO's player motions drive are identical in DDDA's body, the weapon
    joints 150-154 and 55 are rigged differently). ``drop_bones`` removes the tracks of those joint ids.

    ``rebake`` = (source body .mod bytes, destination body .mod bytes[, joint ids]): first re-express
    every joint the two bodies parent differently, and that the motions place, under its destination
    parent, so it keeps its source world transform at every frame -- weapons and the hit shapes on
    them stay where the source game puts them (``retarget.rebake``; ``docs/animation.md``).
    Event and float tracks are kept as they are; what their ids trigger in the other game is UNKNOWN."""
    from . import lmt, lmtcodec

    notes: list[str] = []
    if rebake is not None:
        from . import retarget as rt
        sb, db, *rest = rebake
        data = rt.rebake(data, src, sb, db, rest[0] if rest else None, dst_game=dst, notes=notes)
        if drop_bones:
            src_body, dst_body = rt.body(sb), rt.body(db)
            parents = {dst_body.joints[j].parent for j in rt.reparented(src_body, dst_body)} - {None}
            if drop_bones & parents:
                notes.append(f"joint(s) {sorted(drop_bones & parents)} are dropped but are the destination parents of "
                             "rebaked joints: those now hang off an unanimated parent")
    m = lmt.parse(data)
    if m.version != LMT_VERSION[src]:
        raise RiftError(f"motion list version {m.version} is not {src.upper()}'s ({LMT_VERSION[src]})")
    m.version = LMT_VERSION[dst]
    cache: dict = {}
    seen: set[int] = set()
    recoded = dropped = cleared = dropped_tracks = 0
    worst = 0.0
    for mo in m.motions:
        if mo is None:
            continue
        if dst == "ddda" and mo.flags & _DDO_MOTION_BIT:
            mo.flags &= ~_DDO_MOTION_BIT
            cleared += 1
        if id(mo.tracks) in seen:
            continue
        seen.add(id(mo.tracks))
        if drop_bones:
            before = len(mo.tracks.tracks)
            mo.tracks.tracks = [t for t in mo.tracks.tracks if t.bone not in drop_bones]
            dropped_tracks += before - len(mo.tracks.tracks)
        for t in mo.tracks.tracks:
            if t.codec in (11, 12, 13):
                if t.buffer is None:
                    raise RiftError(f"a codec {t.codec} track without keys (no game file has one); not guessing "
                                    "what the other game would make of it")
                ext = t.extremes.data if t.extremes is not None else None
                if (ext is None) != (src == "ddda"):
                    raise RiftError(f"a codec {t.codec} track {'with' if ext else 'without'} extremes is not how "
                                    f"{src.upper()} stores them; not guessing its values")
                key = (id(t.buffer), id(t.extremes), t.reference)
                if key not in cache:
                    vals = lmtcodec.values(t.codec, t.buffer.data, ext, t.reference)
                    ks = lmtcodec.keys(t.codec, t.buffer.data)
                    new = lmt.Blob(lmtcodec.pack(6, [(d, tuple(_q14(c) for c in q)) for (d, _), (_, q) in zip(ks, vals)]))
                    back = lmtcodec.values(6, new.data)
                    for (_, a), (_, b) in zip(vals, back):
                        worst = max(worst, max(abs(x - y) for x, y in zip(a, b)))
                    cache[key] = new
                t.codec, t.buffer, t.extremes = 6, cache[key], None
                recoded += 1
            elif t.codec == 6 and t.extremes is not None and dst == "ddo":
                t.extremes = None
                dropped += 1
    out = lmt.build(m)
    lmt.parse(out)   # the result must satisfy the strict reader (sharing flags, spans, sizes)
    notes.append(f"version {LMT_VERSION[src]} -> {LMT_VERSION[dst]}; {m.count} motion(s), "
                 f"{len(lmt.bones(m))} bone id(s) driven")
    if recoded:
        notes.append(f"{recoded} single-axis rotation track(s) (codec 11-13) re-encoded as codec 6; largest "
                     f"component change {worst:.6f}")
    if dropped:
        notes.append(f"{dropped} codec 6 track(s) lost unused extremes")
    if cleared:
        notes.append(f"flag bit 0 cleared on {cleared} motion(s) (DDO only; no loader reads it)")
    if drop_bones:
        notes.append(f"{dropped_tracks} track(s) of joint(s) {sorted(drop_bones)[:16]} removed")
    if like is not None:
        try:
            old = lmt.parse(like)
        except RiftError:
            old = None
        if old is not None:
            extra = sorted(set(lmt.bones(m)) - set(lmt.bones(old)))
            if extra:
                notes.append(f"bone ids {extra[:16]}{' ...' if len(extra) > 16 else ''} are not driven by the motion "
                             "list it replaces; check the skeleton (riftstone lmt bones)")
            else:
                notes.append("every bone id it drives is one the replaced motion list drives")
    notes.append("events and float tracks kept as they are; what they trigger in the other game is UNKNOWN")
    return Ported(out, notes)


# -- skeletons -----------------------------------------------------------------------------------
@dataclass
class Joint:
    id: int
    parent: int | None       # the parent's joint id
    offset: tuple[float, float, float]   # from the parent, model units


def skeleton(data: bytes) -> dict[int, Joint]:
    """A model's joints by joint id (what motion tracks address): parent and offset from the parent.
    Bone records are 24 bytes: u8 id, u8 parent index (255 = root), u8 mirror, u8, f32, f32 length,
    f32[3] offset (the section map is model_info's)."""
    info = model_info(data)
    bones = MOD_HEADER.unpack_from(data)[11]
    recs = [struct.unpack_from("<BBBBff3f", data, bones + 24 * i) for i in range(info.bones)]
    out: dict[int, Joint] = {}
    for jid, parent, _mirror, _u, _f, _length, x, y, z in recs:
        pid = recs[parent][0] if parent < len(recs) else None
        out[jid] = Joint(jid, pid, (x, y, z))
    return out


def skeleton_diff(a: bytes, b: bytes, tolerance: float = 0.5) -> dict:
    """Compare two models' skeletons by joint id: shared joints whose parent differs or whose offset
    from the parent differs by more than `tolerance` model units, and the joints only one side has."""
    sa, sb = skeleton(a), skeleton(b)
    common = sorted(set(sa) & set(sb))
    parent = [j for j in common if sa[j].parent != sb[j].parent]
    moved = []
    for j in common:
        d = max(abs(p - q) for p, q in zip(sa[j].offset, sb[j].offset))
        if d > tolerance:
            moved.append((j, round(d, 3)))
    return {"common": len(common), "only_first": sorted(set(sa) - set(sb)), "only_second": sorted(set(sb) - set(sa)),
            "parent_differs": parent, "offset_differs": moved}


# -- collision -----------------------------------------------------------------------------------
def convert_sbc(data: bytes, src: str, dst: str, like: bytes | None = None) -> Ported:
    return Ported(sbc.for_game(data, dst), [
        "collision: the layout is the same in both games, so only the version word changed; the surface "
        "attributes (footing, materials) are copied as they are, and whether the other game reads them "
        "the same way is UNKNOWN"])


# -- entry point ---------------------------------------------------------------------------------
def convert(data: bytes, type_id: int, src: str, dst: str, like: bytes | None = None,
            rebake: tuple | None = None) -> Ported:
    """Convert one resource from game `src` to game `dst` ('ddda' / 'ddo').  `like` is the destination
    resource being replaced, when there is one (its header details are kept where they apply).
    `rebake` (motion lists only): the two bodies to rebake re-parented joints between (convert_lmt)."""
    _check_kinds(src, dst)
    if src == dst:
        return Ported(data, ["same game: copied as is"])
    ext = _EXT.get(type_id)
    if ext is None:
        raise RiftError(f".{ext_of(type_id)} resources do not port between the games yet (textures, text, "
                        "models, materials, motion lists and collision do)")
    if rebake is not None:
        if ext != "lmt":
            raise RiftError("rebaking joints applies to motion lists (.lmt) only")
        return convert_lmt(data, src, dst, like, rebake=rebake)
    return {"tex": convert_tex, "gmd": convert_gmd, "mod": convert_mod, "mrl": convert_mrl,
            "lmt": convert_lmt, "sbc": convert_sbc}[ext](data, src, dst, like)


# -- into a mod (the CLI's `riftstone port` and Studio's Port button) ---------------------------------
@dataclass
class PortResult:
    written: list = field(default_factory=list)   # paths written into the mod
    notes: list[str] = field(default_factory=list)
    textures: list = field(default_factory=list)  # the converted textures a model's material brought along


def _locate(idx, game, rel: str, arc_hint: str | None = None):
    from . import arc as arclib, fsmap

    rel = rel.replace("\\", "/")
    if rel.lower().endswith(".yaml"):
        rel = rel[:-5]
    name, tid = fsmap.decode_path(rel)
    arcs = [arc_hint] if arc_hint else idx.archives_with(name, tid)
    if not arcs:
        raise RiftError(f"no archive of {game.title} contains {rel}")
    e = arclib.Archive.read(game.vanilla_arc(arcs[0])).find(name, tid)
    if e is None:
        raise RiftError(f"{arcs[0]} does not contain {rel}")
    return name, tid, e.data(), arcs


def _write(mod_root, dst_idx, rel: str, data: bytes, tid: int, arcs: list[str] | None) -> list:
    """One ported resource into the mod: over an existing resource (files/) or into archives."""
    from . import arcfolder, fsmap, params

    name, _ = fsmap.decode_path(rel)
    as_yaml = params.is_editable_resource(data, tid)
    payload = params.resource_to_yaml(data, name.decode("latin-1"), tid).encode("utf-8") if as_yaml else data
    suffix = ".yaml" if as_yaml else ""
    if arcs:
        targets = [mod_root / "archives" / (a + ".arc") / (rel + suffix) for a in arcs]
    elif dst_idx.archives_with(name, tid):
        targets = [mod_root / "files" / (rel + suffix)]
    else:
        raise RiftError(f"{rel} does not exist in the destination game: replace an existing resource (--as) "
                        "or add it to an archive (--arc)")
    for t in targets:
        arcfolder.write_file(t, payload)
    return targets


def into_mod(mod_root, src_game, dst_game, src_idx, dst_idx, resource: str, as_: str | None = None,
             like: str | None = None, arc_name: str | None = None, from_arc: str | None = None,
             model_only: bool = False, rebake: bool = False, material: str | None = None,
             alternates: bool = False) -> PortResult:
    """Port `resource` of src_game into the mod at mod_root (a dst_game mod), converted and checked.
    A model brings its material (rebuilt from `like`, default the replaced model's material) and the
    textures that material binds, namespaced under <source game>\\ in the target's archives.
    `material` (a model): take the source material from this engine path in the model's archive instead
    of the one beside the model (a Dragon's Dogma Online variant keeps its own material next to the
    base's model, e.g. the White Chimera).
    `alternates` (a model over another): also rebuild every other material made for the replaced model
    (alternate_materials), so whichever material the game puts on it holds the ported model's materials.
    `rebake` (player motion lists): rebake the re-parented weapon joints between the two games' player
    bodies (PLAYER_BODY), so weapons and their hit shapes keep their place."""
    from . import fsmap

    src, dst = src_game.kind, dst_game.kind
    _check_kinds(src, dst)
    if src == dst:
        raise RiftError("both sides are the same game; use extract")
    name, tid, data, src_arcs = _locate(src_idx, src_game, resource, from_arc)
    rel = fsmap.encode_name(name, tid)
    target = (as_ or rel).replace("\\", "/")
    if target.lower().endswith(".yaml"):
        target = target[:-5]
    tname, ttid = fsmap.decode_path(target)
    if ttid != tid:
        raise RiftError(f"the destination must be a .{typemap.extension(tid)} path like the source")
    arcs = [arc_name] if arc_name else None
    ext = typemap.extension(tid)
    out = PortResult()
    if rebake and ext != "lmt":
        raise RiftError("--retarget applies to motion lists (.lmt) only")
    if (material is not None or alternates) and (ext != "mod" or model_only):
        raise RiftError("a source material or alternate materials go with a model and its parts (not --model-only)")
    if ext == "mrl":
        if not like:
            raise RiftError("a material is rebuilt from one of the destination game's: give a template (--like)")
        _, _, tpl, _ = _locate(dst_idx, dst_game, like)
        done = retarget(tpl, dst, source=data)
        out.written += _write(mod_root, dst_idx, target, done.data, tid, arcs)
        out.notes += done.notes
        return out
    old = _locate(dst_idx, dst_game, target)[2] if dst_idx.archives_with(tname, tid) else None
    bodies = None
    if rebake:
        bodies = (_locate(src_idx, src_game, PLAYER_BODY[src])[2], _locate(dst_idx, dst_game, PLAYER_BODY[dst])[2])
    done = convert(data, tid, src, dst, old, rebake=bodies)
    out.written += _write(mod_root, dst_idx, target, done.data, tid, arcs)
    out.notes += done.notes
    if ext == "mod" and not model_only:
        _model_parts(out, mod_root, src_game, dst_game, src_idx, dst_idx, name, data, tname, src_arcs, arcs, like,
                     material, alternates)
    return out


def covers(model_data: bytes, material_data: bytes) -> bool:
    """A material file holds a material for every material name of a model (hash = JAMCRC(name))."""
    want = {zlib.crc32(n) ^ 0xFFFFFFFF for n in model_info(model_data).material_names}
    return bool(want) and want <= {x.material_hash for x in mrl.parse(material_data).materials}


def alternate_materials(game, idx, model: bytes, own: bytes) -> list[tuple[bytes, bytes, list[str]]]:
    """The other material files made for a model, [(name, data, archives holding it)]: every .mrl in the
    archives that hold the model (except `own`) with a material for each of the model's material names.
    DDDA keeps full-detail ones the game puts on the unit by path (the chimera's e5200_a, docs/enemy-skins.md)
    and variants' own (the Hobgoblin's e0101_a over the goblin model)."""
    from . import arc as arclib

    mod_tid, mrl_tid = typemap.type_for_extension("mod"), typemap.type_for_extension("mrl")
    holders = idx.archives_with(model, mod_tid)
    if not holders:
        return []
    model_data = arclib.Archive.read(game.vanilla_arc(holders[0])).find(model, mod_tid).data()
    out, seen = [], {own}
    for a in holders:
        archive = None
        for r in idx.entries(a):
            if r["type"] != mrl_tid or r["name"] in seen:
                continue
            seen.add(r["name"])
            archive = archive or arclib.Archive.read(game.vanilla_arc(a))
            e = archive.find(r["name"], mrl_tid)
            try:
                if e is not None and covers(model_data, e.data()):
                    out.append((r["name"], e.data(), idx.archives_with(r["name"], mrl_tid)))
            except RiftError:
                continue
    return out


def _model_parts(out, mod_root, src_game, dst_game, src_idx, dst_idx, name, data, tname, src_arcs, arcs, like,
                 material=None, alternates=False):
    from . import arc as arclib, arcfolder, fsmap

    src, dst = src_game.kind, dst_game.kind
    mrl_tid, tex_tid = typemap.type_for_extension("mrl"), typemap.type_for_extension("tex")
    mname = name
    if material is not None:
        mpath = material.replace("\\", "/")
        mname, mtid = fsmap.decode_path(mpath[:-5] if mpath.lower().endswith(".yaml") else mpath)
        if mtid != mrl_tid:
            raise RiftError(f"{material} is not a material (.mrl)")
    e = arclib.Archive.read(src_game.vanilla_arc(src_arcs[0])).find(mname, mrl_tid)
    if e is None:
        out.notes.append(f"no {mname.decode('latin-1')}.mrl {'beside the model ' if material is None else ''}in "
                         f"{src_arcs[0]}; material not ported")
        return
    target_mrl = fsmap.encode_name(tname, mrl_tid)
    like = like or target_mrl
    if not dst_idx.archives_with(fsmap.decode_path(like)[0], mrl_tid):
        out.notes.append(f"no destination material {like} to use as a template: give one (--like)")
        return
    _, _, tpl, tpl_arcs = _locate(dst_idx, dst_game, like)
    homes = list(arcs or dst_idx.archives_with(tname, mrl_tid) or tpl_arcs)

    def rename(p: str) -> str | None:   # textures that are real texture files of the source game move
        return src + "\\" + p if src_idx.archives_with(p.encode("latin-1"), tex_tid) else None

    names = model_info(data).material_names
    done = retarget(tpl, dst, source=e.data(), material_names=names, rename=rename)
    rebuilt = [(target_mrl, done)]
    if alternates:      # every other material made for the replaced model, so whichever the game puts on it fits
        for n, alt, holders in alternate_materials(dst_game, dst_idx, tname, fsmap.decode_path(target_mrl)[0]):
            rebuilt.append((fsmap.encode_name(n, mrl_tid), retarget(alt, dst, source=e.data(), material_names=names,
                                                                    rename=rename)))
            if not arcs:
                homes += [h for h in holders if h not in homes]
            out.notes.append(f"{n.decode('latin-1')} is a material made for the same model (one for each of its "
                             "material names): rebuilt the same way")
    wanted = list(dict.fromkeys(p for _, d in rebuilt for p in used_textures(d.data) if p.startswith(src + "\\")))
    for new in wanted:
        tn = new[len(src) + 1:].encode("latin-1")
        where = src_idx.archives_with(tn, tex_tid)
        if len(new.encode("latin-1")) >= 64:
            raise RiftError(f"{new} is too long for an archive entry (63 characters at most)")
        conv = convert_tex(arclib.Archive.read(src_game.vanilla_arc(where[0])).find(tn, tex_tid).data(), src, dst)
        for h in homes:
            t = mod_root / "archives" / (h + ".arc") / fsmap.encode_name(new.encode("latin-1"), tex_tid)
            arcfolder.write_file(t, conv.data)
            out.textures.append(t)
        out.notes += conv.notes
    for path, d in rebuilt:
        for w in _write(mod_root, dst_idx, path, d.data, mrl_tid, arcs):
            out.notes.append(f"material -> {w}")
            if path != target_mrl:
                out.written.append(w)
    out.notes.append(f"{len(wanted)} texture(s) converted into {len(homes)} archive(s) under {src}\\")
    out.notes += done.notes

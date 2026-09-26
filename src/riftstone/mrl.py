"""rMaterial (``.mrl``): MT Framework material list, Dragon's Dogma revision 0x20.  A model's
materials -- which **shader** each one uses and which **textures** it binds.  This is the format
behind "see what shaders are used where" and behind repointing a material's textures.

Header (little-endian), proved byte-exact on every ``.mrl`` in the game (`check_corpus --only mrl`):

  u32 magic "MRL\\0"
  u32 version (0x20)
  u32 matCount
  u32 texCount
  u32 typeHash                 rMaterial DTI/version hash (constant per build)
  u32 texTableOff (0x1C)       -> texture table
  u32 matTableOff              -> material table  (== texTableOff + texCount*0x4C)
  texCount x 0x4C:  u32 type_id (rTexture 0x241F5DEB), u32, u32, char name[0x40]
  matCount x 0x3C:  u32 shaderHash, u32 materialHash, u32 paramOff, u32 h0C, u32 h10,
                    u32 ref14, u32 ref18, u32 x1C, u32 z20..z30 (5), u32 paramOff2, u32 z38
  <shader-parameter data>      kept opaque -> exact rebuild

The parameter tail is kept verbatim, so ``build`` reproduces the original bytes for every material
file.  Texture names (fixed 0x40 field) and shader/material hashes are editable in place without
moving anything, so an edit stays byte-exact in layout.  Full per-material parameter editing (the
`cDDMaterialCtrl` getShader* block) is a later layer; the read side already answers "what shaders/
textures are used where".
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError

MAGIC = 0x004C524D  # "MRL\0"
VERSION = 0x20
VERSION_DDO = 0x22  # Dragon's Dogma Online: the same header, 0x4C texture and 0x3C material records
                    # (measured on its 6,550 distinct files); the parameter tail stays opaque
VERSIONS = (VERSION, VERSION_DDO)
TAG = "mrl/0x20"
_HDR = struct.Struct("<IIIIIII")   # magic, ver, matCount, texCount, typeHash, texOff, matOff
TEX_ENTRY = 0x4C
MAT_ENTRY = 0x3C
_TEX = struct.Struct("<III")       # type_id, a, b  (name[0x40] follows)
NAME_LEN = 0x40
_MAT = struct.Struct("<15I")
TEX_TYPE_ID = 0x241F5DEB           # rTexture


@dataclass
class Texture:
    type_id: int
    a: int
    b: int
    raw_name: bytes      # the exact 0x40-byte field (NUL-terminated, then original padding)

    @property
    def name(self) -> str:
        return self.raw_name.split(b"\0", 1)[0].decode("latin-1")

    def set_name(self, new: str) -> None:
        nb = new.encode("latin-1")
        if len(nb) >= NAME_LEN:
            raise FormatError("mrl", f"texture name too long (max {NAME_LEN - 1}): {new!r}")
        self.raw_name = nb + b"\0" * (NAME_LEN - len(nb))


@dataclass
class Material:
    shader: int          # shader hash -- which shader this material uses
    material_hash: int
    fields: list         # the remaining 13 u32 (param offsets, sub-hashes, refs) -- kept verbatim


@dataclass
class Mrl:
    version: int
    type_hash: int
    textures: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    tail: bytes = b""    # shader-parameter data after the material table (opaque)


def parse(data: bytes) -> Mrl:
    if len(data) < _HDR.size:
        raise FormatError("mrl", "too short for a header")
    magic, ver, mat_n, tex_n, thash, tex_off, mat_off = _HDR.unpack_from(data, 0)
    if magic != MAGIC:
        raise FormatError("mrl", "not an MRL\\0 material")
    if ver not in VERSIONS:
        raise FormatError("mrl", f"revision 0x{ver:x}; Dragon's Dogma is 0x20, Online 0x22")
    if tex_off != _HDR.size:
        raise FormatError("mrl", f"unexpected texture-table offset 0x{tex_off:x}")
    if mat_off != tex_off + tex_n * TEX_ENTRY:
        raise FormatError("mrl", "material-table offset does not follow the texture table")
    need = mat_off + mat_n * MAT_ENTRY
    if len(data) < need:
        raise FormatError("mrl", "truncated material/texture table")
    textures = []
    for i in range(tex_n):
        o = tex_off + i * TEX_ENTRY
        tid, a, b = _TEX.unpack_from(data, o)
        raw = bytes(data[o + 12:o + 12 + NAME_LEN])
        textures.append(Texture(tid, a, b, raw))
    materials = []
    for i in range(mat_n):
        vals = _MAT.unpack_from(data, mat_off + i * MAT_ENTRY)
        materials.append(Material(vals[0], vals[1], list(vals[2:])))
    return Mrl(ver, thash, textures, materials, bytes(data[need:]))


def build(m: Mrl) -> bytes:
    tex_off = _HDR.size
    mat_off = tex_off + len(m.textures) * TEX_ENTRY
    out = bytearray(_HDR.pack(MAGIC, m.version, len(m.materials), len(m.textures),
                              m.type_hash, tex_off, mat_off))
    for t in m.textures:
        if len(t.raw_name) != NAME_LEN:
            raise FormatError("mrl", f"texture name field must be {NAME_LEN} bytes")
        out += _TEX.pack(t.type_id, t.a, t.b) + t.raw_name
    for mat in m.materials:
        if len(mat.fields) != 13:
            raise FormatError("mrl", "material record must keep its 13 trailing fields")
        out += _MAT.pack(mat.shader, mat.material_hash, *mat.fields)
    out += m.tail
    return bytes(out)


def info(m: Mrl) -> str:
    lines = [f"material list: {len(m.materials)} material(s), {len(m.textures)} texture(s), rev 0x{m.version:x}"]
    for i, t in enumerate(m.textures):
        kind = "tex" if t.type_id == TEX_TYPE_ID else f"type {t.type_id:#010x}"
        lines.append(f"  texture[{i}] {kind}  {t.name}")
    for i, mat in enumerate(m.materials):
        lines.append(f"  material[{i}] shader {mat.shader:#010x}  id {mat.material_hash:#010x}")
    return "\n".join(lines)


# -- structured layer: resource bindings and block layout (used to rebuild materials) -------------
# Each material's command block starts with numResources (bits 0-11 of its u32 at 0x18) bindings of
# 12 bytes: u32 (type:4 | 0xDCDC:16 | shader-object index:12), u32 value, u32 shader object id
# (JAMCRC(name) & 0xFFFFF) << 12 | index.  For a texture binding (type 3) the value is the 1-based
# index into the texture table (0: the dummy texture).  Constant-buffer values are offsets from the
# block's start, so a block moves as a unit.  The games lay a file out as: tables, padding to 16,
# every command block in material order, then every animation block (checked on the corpus).
CMD = struct.Struct("<III")
SET_TEXTURE = 3
_SLOTS = ("tAlbedoMap", "tAlbedoBlendMap", "tNormalMap", "tNormalBlendMap", "tSpecularMap", "tSpecularBlendMap",
          "tDetailNormalMap", "tDetailNormalMap2", "tDetailMaskMap", "tTransparencyMap", "tOcclusionMap",
          "tHeightMap", "tHairShiftMap", "tLightMap", "tLightMaskMap", "tIndirectMap", "tIndirectMaskMap",
          "tEnvMap", "tSphereMap", "tFresnelMap", "tBaseMap", "tCubeMap", "tBlendMap", "tVtxDisplacement",
          "tVtxDispMask", "tThinMap", "tGlobalEnvMap")


def _jam20(name: str) -> int:
    import zlib

    return (zlib.crc32(name.encode()) ^ 0xFFFFFFFF) & 0xFFFFF


SLOT_NAMES = {_jam20(n): n for n in _SLOTS}


@dataclass
class Binding:
    word0: int
    value: int
    obj: int

    @property
    def kind(self) -> int:
        return self.word0 & 0xF

    @property
    def slot(self) -> int:
        return self.obj >> 12

    @property
    def slot_name(self) -> str:
        return SLOT_NAMES.get(self.slot, f"0x{self.slot:05x}")


def bindings(raw: bytes, mat: Material) -> list[Binding]:
    off, count = mat.fields[11], mat.fields[4] & 0xFFF
    if off + count * CMD.size > len(raw):
        raise FormatError("mrl", "a material's bindings run past the end of the file")
    return [Binding(*CMD.unpack_from(raw, off + i * CMD.size)) for i in range(count)]


def blocks(raw: bytes, m: Mrl) -> list[tuple[bytes, bytes]]:
    """Each material's (command block, animation block).  A block runs to the next block (or the file
    end), so data a material reaches past its stated size travels with it."""
    starts = sorted({x.fields[11] for x in m.materials} | {x.fields[12] for x in m.materials if x.fields[10]}
                    | {len(raw)})

    def extent(o: int) -> bytes:
        return raw[o:next((s for s in starts if s > o), len(raw))]

    return [(extent(x.fields[11]), extent(x.fields[12]) if x.fields[10] else b"") for x in m.materials]


def assemble(version: int, type_hash: int, textures: list[Texture], materials: list[Material],
             parts: list[tuple[bytes, bytes]]) -> bytes:
    """A material file laid out the games' way from records and their (command, animation) blocks."""
    import copy

    tex_off = _HDR.size
    mat_off = tex_off + len(textures) * TEX_ENTRY
    pos = mat_off + len(materials) * MAT_ENTRY
    pos += -pos % 16
    body = bytearray()
    cmd_at = []
    for cmd, _ in parts:
        cmd_at.append(pos + len(body))
        body += cmd
    anim_at = []
    for _, anim in parts:
        anim_at.append(pos + len(body) if anim else None)
        body += anim
    recs = []
    for mat, c, a in zip(materials, cmd_at, anim_at):
        r = copy.deepcopy(mat)
        r.fields[11] = c
        if a is not None:
            r.fields[12] = a
        recs.append(r)
    head = build(Mrl(version, type_hash, textures, recs, b""))
    return head + bytes(pos - len(head)) + bytes(body)


def rebuild(raw: bytes) -> bytes:
    """parse + assemble: equal to the input for every file laid out the games' usual way."""
    m = parse(raw)
    return assemble(m.version, m.type_hash, m.textures, m.materials, blocks(raw, m))

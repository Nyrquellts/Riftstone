"""Dragon's Dogma Online's equipment dye, baked into colour maps Dark Arisen can draw.

Online colours its equipment when it draws it.  Measured on client 03.04.003 (``docs/ddo-dye.md``;
``tools/ddo_dye_proof.py`` repeats every number):

* The material, class ``nDraw::DDMrlStdEstObj`` (4,610 of the client's 38,953 materials, every equipment
  folder), binds ``tAlbedoMap`` (``<part>_NUKI``: the colour map, its alpha the cut-out), ``tColorMaskMap``
  (``<part>_CMM``: a mask in each of R, G and B) and a constant buffer ``CBColorMask`` of five rows:
  ``fColorMaskRate``, ``fColorMaskThreshold``, ``fColorMaskColor1..3``.
* Every pixel shader that samples ``tColorMaskMap`` (10 in ``sc\\DX9\\root``, read with D3DDisassemble and
  run on random inputs by the proof tool) computes, on the colour map squared (its linear light)::

      w  = (1 - mask.rgb) * rate
      a *= lerp(1, colour1, w.r)
      a *= lerp(1, colour2, w.g)    where w.r <= threshold.x
      a *= lerp(1, colour3, w.b)    where w.r <= threshold.y and w.g <= threshold.z

  and adds a view-dependent sheen (``tAlbedoBlendMap`` looked up by the view-space normal), which is lighting,
  not colour, and is not baked.
* The colours an item wears come from its model's montage (``.dmt``, rDDOModelMontage, version 0x11): a
  table of variants x entries, each entry five float4 rows and the model material it colours.  DDO.exe
  (rDDOModelMontage's colour routine, unpacked dump 0x00A69A70) copies row 0 into the specular colour
  (constant buffer 0x7B2C2, row 10), row 1 into CBMaterial row 1 (the environment-map tint) and rows 2-4
  into fColorMaskColor1-3; rate and threshold stay the material's.  The variant is the colour number modulo
  the variant count; a montage of kind 9 colours only when forced.
* An item's colour number is its group's ``mModelBase.mColorNo`` in ``etc\\itemlist.ipa`` (the item list,
  read here completely: ``read_itemlist``); the group's model tag leads through ``etc\\wepResTable.wrt`` to
  the model and montage.  The dyes red, green, blue, yellow, pink and black are variants 10-15 (their
  colours' hues, measured on every montage).

Baking multiplies each texel by the square root of that factor, channel by channel: exact in Online's own
convention (the shader squares the map, multiplies, lights the product).  Alpha stays; every 4x4 block,
at every mip, whose texels no dye reaches keeps its bytes; format, size and mip count stay the map's.  How
the result looks in Dark Arisen: UNKNOWN until played.
"""
from __future__ import annotations

import math
import re
import struct
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import mrl, tex, texcodec, typemap
from .errors import FormatError, RiftError

# -- engine ids -----------------------------------------------------------------------------------
SLOT_ALBEDO = mrl._jam20("tAlbedoMap")
SLOT_MASK = mrl._jam20("tColorMaskMap")          # 0x383E5
CB_COLOR_MASK = mrl._jam20("CBColorMask")        # 0x6F016
CB_MATERIAL = mrl._jam20("CBMaterial")           # 0x6C801
CB_GLOBALS = 0x7B2C2                             # its name is not in the client; the shaders read its rows
#                                                  0, 9 and 10 as Globals__packed0/9/10 (row 10 = specular)
SET_CONSTANTS = 1                                # a binding whose value is a constant buffer's offset
MODEL_CLASS = 0x655A905B                         # nDraw::DDMrlStdEstObj (the only class that binds the mask)

MONTAGE_MAGIC = b"DMT\0"
MONTAGE_VERSION = 0x11
MONTAGE_ENTRY = 0x60                              # 5 float4 rows, the model material's index, 12 zero bytes
KIND_FORCED_ONLY = 9                              # rDDOModelMontage vf19 skips these unless forced
DYES = {"red": 10, "green": 11, "blue": 12, "yellow": 13, "pink": 14, "black": 15}

MOD_TYPE, MRL_TYPE, TEX_TYPE = (typemap.BY_EXT[e] for e in ("mod", "mrl", "tex"))
DMT_TYPE = typemap.type_for_extension("dmt")
ITL_TYPE = typemap.type_for_extension("itl")
WRT_TYPE = typemap.type_for_extension("wrt")
GMD_TYPE = typemap.BY_EXT["gmd"]
ITEM_LIST = b"etc\\itemlist"
RES_TABLE = b"etc\\wepResTable"
ITEM_NAMES = b"ui\\00_message\\common\\item_name"


def jamcrc(name: bytes) -> int:
    return zlib.crc32(name) ^ 0xFFFFFFFF


# -- the colour mask ------------------------------------------------------------------------------
@dataclass(frozen=True)
class ColourMask:
    """One material's CBColorMask: rate, threshold and the three colours (linear, as the shader uses them)."""
    rate: tuple
    threshold: tuple
    colours: tuple                 # ((r, g, b), (r, g, b), (r, g, b))

    def check(self) -> "ColourMask":
        vals = list(self.rate) + list(self.threshold) + [v for c in self.colours for v in c]
        if len(self.rate) != 3 or len(self.threshold) != 3 or len(self.colours) != 3 or \
                any(len(c) != 3 for c in self.colours):
            raise RiftError("a colour mask has three rates, three thresholds and three colours")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals):
            raise RiftError("a colour mask value is not a finite number")
        return self

    def factors(self, m0: float, m1: float, m2: float) -> tuple[float, float, float]:
        """The linear multipliers for mask values m (0..1 per channel), as every colour-mask shader computes."""
        r, t, (c1, c2, c3) = self.rate, self.threshold, self.colours
        w1, w2, w3 = (1.0 - m0) * r[0], (1.0 - m1) * r[1], (1.0 - m2) * r[2]
        out = [1.0 + w1 * (c1[i] - 1.0) for i in range(3)]
        if t[0] - w1 >= 0:
            out = [out[i] * (1.0 + w2 * (c2[i] - 1.0)) for i in range(3)]
        if t[1] - w1 >= 0 and t[2] - w2 >= 0:
            out = [out[i] * (1.0 + w3 * (c3[i] - 1.0)) for i in range(3)]
        return out[0], out[1], out[2]

    def key(self) -> bytes:
        """Bytes that name this colour set (for the dyed map's name)."""
        return struct.pack("<15d", *self.rate, *self.threshold, *(v for c in self.colours for v in c))


def hex_of(colour) -> str:
    """A linear colour as the texel colour it multiplies by (#rrggbb; values past 1 are shown as 1)."""
    return "#" + "".join(f"{round(255 * math.sqrt(min(1.0, max(0.0, v)))):02x}" for v in colour)


def colour_of_hex(text: str) -> tuple[float, float, float]:
    """'#rrggbb' (a texel multiplier) as the linear colour the shader would use: (v/255)^2."""
    s = text.strip()
    if not re.fullmatch(r"#?[0-9A-Fa-f]{6}", s, re.ASCII):
        raise RiftError(f"{text!r} is not a colour; write it as #rrggbb")
    s = s.lstrip("#")
    return tuple((int(s[i:i + 2], 16) / 255.0) ** 2 for i in (0, 2, 4))


# -- the montage (.dmt) -----------------------------------------------------------------------------
@dataclass(frozen=True)
class Entry:
    """One montage colour entry: five float4 rows and the model material (index) it colours."""
    material: int
    rows: tuple                    # 20 floats

    @property
    def empty(self) -> bool:
        return not any(self.rows)

    @property
    def specular(self) -> tuple:   # row 0 -> constant buffer 0x7B2C2 row 10
        return self.rows[0:3]

    @property
    def env(self) -> tuple:        # row 1 -> CBMaterial row 1
        return self.rows[4:7]

    @property
    def colours(self) -> tuple:    # rows 2-4 -> fColorMaskColor1-3
        return self.rows[8:11], self.rows[12:15], self.rows[16:19]


@dataclass
class Montage:
    kind: int
    variants: int
    per_variant: int
    entries: list                  # variants * per_variant Entry

    def variant(self, n: int) -> list:
        """The entries colour number n applies: variant n modulo the count, as DDO.exe takes it."""
        if not self.variants:
            return []
        v = n % self.variants
        return self.entries[v * self.per_variant:(v + 1) * self.per_variant]

    def used(self) -> list[int]:
        """Variants with any colour (an all-zero variant would paint the dyed parts black).  A montage with no
        entries per variant colours nothing, however many variants it counts."""
        if not self.per_variant:
            return []
        return [v for v in range(self.variants) if any(not e.empty for e in self.variant(v))]


_MONTAGE_HEAD = struct.Struct("<4s8I9I")


def parse_montage(data: bytes) -> Montage:
    """The colour table of an rDDOModelMontage (DDO.exe's loader accepts only version 0x11; its nine table
    offsets follow the counts).  Only what dyeing reads is kept: kind, variants and entries."""
    if len(data) < _MONTAGE_HEAD.size:
        raise FormatError("dmt", "too short for a montage header")
    head = _MONTAGE_HEAD.unpack_from(data, 0)
    magic, version, kind, _parts, _n, _patterns, variants, per_variant, _zero = head[:9]
    offsets = head[9:]
    if magic != MONTAGE_MAGIC:
        raise FormatError("dmt", "not a DMT montage")
    if version != MONTAGE_VERSION:
        raise FormatError("dmt", f"version 0x{version:x}; Online's loader takes only 0x11")
    if any(o > len(data) for o in offsets):
        raise FormatError("dmt", "a table offset points past the end")
    table = offsets[3]
    count = variants * per_variant
    if count and (table < _MONTAGE_HEAD.size or table + count * MONTAGE_ENTRY > len(data)):
        raise FormatError("dmt", f"{variants} variant(s) of {per_variant} entries do not fit the colour table")
    entries = []
    for k in range(count):
        o = table + k * MONTAGE_ENTRY
        rows = struct.unpack_from("<20f", data, o)
        entries.append(Entry(struct.unpack_from("<I", data, o + 0x50)[0], rows))
    return Montage(kind, variants, per_variant, entries)


def build_montage(kind: int, variants: int, per_variant: int, entries: list) -> bytes:
    """A montage holding only a colour table (for tests and fuzz seeds: the game's own also hold the part
    tables that switch meshes, which dyeing never reads).  parse_montage reads it back to the same entries."""
    if len(entries) != variants * per_variant:
        raise RiftError(f"{variants} variant(s) of {per_variant} entries need {variants * per_variant} entries")
    table = _MONTAGE_HEAD.size
    end = table + len(entries) * MONTAGE_ENTRY
    out = bytearray(_MONTAGE_HEAD.pack(MONTAGE_MAGIC, MONTAGE_VERSION, kind, 0, 0, 0, variants, per_variant, 0,
                                       table, table, table, table, end, end, end, end, end))
    for e in entries:
        out += struct.pack("<20fI", *e.rows, e.material) + bytes(12)
    return bytes(out)


# -- materials --------------------------------------------------------------------------------------
@dataclass
class MaskedMaterial:
    index: int                     # in the .mrl
    material_hash: int             # JAMCRC of the model's material name
    albedo: str                    # tAlbedoMap's texture
    mask: str                      # tColorMaskMap's texture
    own: ColourMask                # CBColorMask as the file has it
    specular: tuple | None         # constant buffer 0x7B2C2 row 10 (rgb)
    env: tuple | None              # CBMaterial row 1 (rgb)


def _floats(raw: bytes, at: int, n: int) -> tuple:
    if at < 0 or at + 4 * n > len(raw):
        raise FormatError("mrl", "a constant buffer runs past the end of the file")
    return struct.unpack_from(f"<{n}f", raw, at)


def masked_materials(raw: bytes) -> dict[int, MaskedMaterial]:
    """Every material of a .mrl that binds a colour mask map and CBColorMask, by material hash."""
    m = mrl.parse(raw)
    out: dict[int, MaskedMaterial] = {}
    for i, x in enumerate(m.materials):
        binds = {}
        for b in mrl.bindings(raw, x):
            binds.setdefault((b.kind, b.slot), b)
        alb, msk = binds.get((mrl.SET_TEXTURE, SLOT_ALBEDO)), binds.get((mrl.SET_TEXTURE, SLOT_MASK))
        cb = binds.get((SET_CONSTANTS, CB_COLOR_MASK))
        if alb is None or msk is None or cb is None:
            continue
        if not (0 < alb.value <= len(m.textures) and 0 < msk.value <= len(m.textures)):
            continue
        base = x.fields[11]
        v = _floats(raw, base + cb.value, 20)
        own = ColourMask(v[0:3], v[4:7], (v[8:11], v[12:15], v[16:19]))
        glob, mat = binds.get((SET_CONSTANTS, CB_GLOBALS)), binds.get((SET_CONSTANTS, CB_MATERIAL))
        spec = _floats(raw, base + glob.value + 0xA0, 3) if glob is not None else None
        env = _floats(raw, base + mat.value + 0x10, 3) if mat is not None else None
        out.setdefault(x.material_hash, MaskedMaterial(i, x.material_hash, m.textures[alb.value - 1].name,
                                                       m.textures[msk.value - 1].name, own, spec, env))
    return out


# -- the item list (etc\itemlist.ipa) and the model table (etc\wepResTable.wrt) ----------------------------
# Every record as DDO.exe's readers take it (rItemList's loader 0x00A972A0 in the unpacked dump; each class's
# reader is its vtable slot 5).  Packed little-endian, no padding; a count byte then that many parameters.
IPA_MAGIC, IPA_VERSION = 0x617069, 0x44
_R_PARAM = struct.Struct("<hHHH")                                   # rItemList::rParam
IPA_CLASSES = (                                                     # (name, fields, parameter kind)
    ("use", "<IHBIBHBIIIBB", "param"),                              # cItemUse
    ("material", "<IHBIBHBIIIB", "param"),                          # cItemMaterial
    ("key", "<IHBIBHBIIB", None),                                   # cItemKey
    ("job", "<IHBIBHBIIIBH", "param"),                              # cItemJob
    ("normal", "<IHBIBHBIIB", None),                                # cItemNormal
    ("weapon", "<IHBIIIHHHBH", "s8"),                               # cItemEquipWeapon (last: its group)
    ("weapon group", "<IBBIHHHBBBBBB", None),                       # cItemEquipWeaponGroup
    ("armour", "<IHBIIIHHHHHBH", "s8"),                             # cItemEquipProtector (last: its group)
    ("armour group", "<IBBIHHHBBB", None),                          # cItemEquipProtectorGroup
    ("jewel", "<IHBIIIIHHHHHHHBBB", "s8"),                          # cItemEquipJewelry
    ("npc", "<IHBIBBIBB", None),                                    # cItemEquipNpcProtector
)
_STRUCTS = [struct.Struct(f) for _, f, _ in IPA_CLASSES]


@dataclass
class ItemList:
    version: int
    param_slots: int               # header count of rParam slots (every item's parameters, in order)
    s8_slots: int                  # header count of rEquipParamS8 slots
    index: list                    # the u32 table after the header
    records: dict                  # class name -> [(fields tuple, params list | None)]

    def groups(self, kind: str) -> list:
        return self.records[kind + " group"]


def _s8(data: bytes, p: int) -> tuple[tuple, int]:
    if p + 2 > len(data):
        raise FormatError("itl", "an equipment parameter runs past the end", p)
    kind, form = data[p], data[p + 1]
    p += 2
    if form in (0, 1):
        if p + 1 > len(data):
            raise FormatError("itl", "an equipment parameter runs past the end", p)
        return (kind, form, data[p]), p + 1
    if form in (2, 3):
        if p + 2 > len(data):
            raise FormatError("itl", "an equipment parameter runs past the end", p)
        return (kind, form, struct.unpack_from("<h" if form == 2 else "<H", data, p)[0]), p + 2
    return (kind, form, None), p


def read_itemlist(data: bytes) -> ItemList:
    """Online's item list, every record (byte-exact: build_itemlist gives the same bytes back)."""
    if len(data) < 64 or struct.unpack_from("<I", data, 0)[0] != IPA_MAGIC:
        raise FormatError("itl", "not an Online item list (ipa)")
    version = struct.unpack_from("<I", data, 4)[0]
    if version != IPA_VERSION:
        raise FormatError("itl", f"item list version 0x{version:x}; client 03.04.003 writes 0x44")
    counts = struct.unpack_from("<14I", data, 8)
    p = 64
    if counts[2] * 4 > len(data) - p:
        raise FormatError("itl", "the index table runs past the end")
    index = list(struct.unpack_from(f"<{counts[2]}I", data, p))
    p += 4 * counts[2]
    records: dict = {}
    used_params = used_s8 = 0
    for (name, _, kind), st, n in zip(IPA_CLASSES, _STRUCTS, counts[3:]):
        if n * st.size > len(data) - p:
            raise FormatError("itl", f"{n} {name} record(s) do not fit", p)
        rows = []
        for _ in range(n):
            if p + st.size > len(data):
                raise FormatError("itl", f"a {name} record runs past the end", p)
            fields = st.unpack_from(data, p)
            p += st.size
            params = None
            if kind is not None:
                if p >= len(data):
                    raise FormatError("itl", f"a {name} record's parameter count runs past the end", p)
                count = data[p]
                p += 1
                params = []
                for _ in range(count):
                    if kind == "param":
                        if p + _R_PARAM.size > len(data):
                            raise FormatError("itl", "an item parameter runs past the end", p)
                        params.append(_R_PARAM.unpack_from(data, p))
                        p += _R_PARAM.size
                    else:
                        v, p = _s8(data, p)
                        params.append(v)
                if kind == "param":
                    used_params += count
                else:
                    used_s8 += count
            rows.append((fields, params))
        records[name] = rows
    if p != len(data):
        raise FormatError("itl", f"{len(data) - p} byte(s) after the last record", p)
    if used_params > counts[0] or used_s8 > counts[1]:
        raise FormatError("itl", "the items hold more parameters than the header makes room for")
    return ItemList(version, counts[0], counts[1], index, records)


def build_itemlist(il: ItemList) -> bytes:
    counts = [il.param_slots, il.s8_slots, len(il.index)] + [len(il.records[name]) for name, _, _ in IPA_CLASSES]
    out = bytearray(struct.pack("<II14I", IPA_MAGIC, il.version, *counts))
    out += struct.pack(f"<{len(il.index)}I", *il.index)
    for (name, _, kind), st in zip(IPA_CLASSES, _STRUCTS):
        for fields, params in il.records[name]:
            out += st.pack(*fields)
            if kind is None:
                continue
            out.append(len(params))
            for prm in params:
                if kind == "param":
                    out += _R_PARAM.pack(*prm)
                else:
                    k, form, value = prm
                    out += bytes((k, form))
                    if form in (0, 1):
                        out.append(value)
                    elif form in (2, 3):
                        out += struct.pack("<h" if form == 2 else "<H", value)
    return bytes(out)


@dataclass(frozen=True)
class ResEntry:
    tag: int                       # the model tag an item group names
    arc: str                       # its archive (armor\<arc> or wp\<arc>)
    sex: int                       # 0 either, 1 male body, 2 female body
    refs: tuple                    # 7 (type id << 32 | JAMCRC of the resource name), 0 = none


def read_restable(data: bytes) -> tuple[int, list[ResEntry]]:
    """rWeaponResTable (``etc\\wepResTable.wrt``): version, then records as cWeaponResTable's reader takes
    them (u32 tag, NUL-terminated archive name, u32 sex, seven u64 references)."""
    if len(data) < 8:
        raise FormatError("wrt", "too short for a header")
    version, n = struct.unpack_from("<II", data, 0)
    if n * 65 > len(data) - 8:
        raise FormatError("wrt", f"{n} record(s) do not fit")
    p, out = 8, []
    for _ in range(n):
        if p + 4 > len(data):
            raise FormatError("wrt", "a record runs past the end", p)
        tag = struct.unpack_from("<I", data, p)[0]
        end = data.find(b"\0", p + 4)
        if end < 0 or end - (p + 4) > 255:
            raise FormatError("wrt", "an archive name has no end", p + 4)
        name = data[p + 4:end].decode("latin-1")
        p = end + 1
        if p + 60 > len(data):
            raise FormatError("wrt", "a record runs past the end", p)
        sex = struct.unpack_from("<I", data, p)[0]
        refs = struct.unpack_from("<7Q", data, p + 4)
        p += 60
        out.append(ResEntry(tag, name, sex, refs))
    if p != len(data):
        raise FormatError("wrt", f"{len(data) - p} byte(s) after the last record", p)
    return version, out


def build_restable(version: int, entries: list[ResEntry]) -> bytes:
    out = bytearray(struct.pack("<II", version, len(entries)))
    for e in entries:
        out += struct.pack("<I", e.tag) + e.arc.encode("latin-1") + b"\0" + struct.pack("<I7Q", e.sex, *e.refs)
    return bytes(out)


# -- what people ask for: a colour ----------------------------------------------------------------------
@dataclass(frozen=True)
class Spec:
    kind: str                      # default | material | variant | rgb
    variant: int | None = None
    colours: tuple | None = None   # rgb: three linear colours
    label: str = ""


def parse_spec(text: str | None) -> Spec:
    """default (the item's own colour) | material (the material file's colours, no montage) | a dye (red, green,
    blue, yellow, pink, black) | a colour number (0-255) | #rrggbb (every dyed part) | #rrggbb,#rrggbb,#rrggbb
    (the three masks' colours)."""
    s = (text or "default").strip().lower()
    if s in ("", "default", "item"):
        return Spec("default", label="default")
    if s in ("material", "mrl", "own"):
        return Spec("material", label="material")
    if s in DYES:
        return Spec("variant", DYES[s], label=s)
    if s in ("rainbow", "restorer", "color restorer", "colour restorer"):
        raise RiftError("the rainbow dye picks a colour in the game and the restorer puts the item's own back: "
                        "choose a dye (red, green, blue, yellow, pink, black), default, or a colour number")
    if re.fullmatch(r"[0-9]{1,3}", s, re.ASCII):
        n = int(s)
        if n > 255:
            raise RiftError("a colour number is 0 to 255")
        return Spec("variant", n, label=f"colour {n}")
    parts = [p for p in s.split(",") if p.strip()]
    if len(parts) in (1, 3) and all(re.fullmatch(r"\s*#?[0-9a-f]{6}\s*", p, re.ASCII) for p in parts):
        cols = tuple(colour_of_hex(p) for p in parts)
        cols = cols * 3 if len(cols) == 1 else cols
        return Spec("rgb", colours=cols, label="#" + "-".join(p.strip().lstrip("#") for p in parts))
    raise RiftError(f"{text!r} is not a colour: default, material, red, green, blue, yellow, pink, black, a colour "
                    "number, #rrggbb or three of them (#r1g1b1,#r2g2b2,#r3g3b3)")


def plan_colours(masked: dict[int, MaskedMaterial], material_names: list[bytes] | None, montage: Montage | None,
                 spec: Spec, default: int | None = None) -> tuple[dict[int, ColourMask], list[str]]:
    """The colour mask each colour-mask material gets for a spec, by material hash (as the game would set it),
    and notes.  A montage entry names a model material by index: ``material_names`` are the model's."""
    notes: list[str] = []
    if not masked:
        return {}, ["no material of this model has a colour mask (nothing to dye)"]
    kind, number = spec.kind, spec.variant
    if kind == "default":
        if montage is None or not montage.variants:
            notes.append("no montage colours for this model: the material file's own colours")
            kind = "material"
        elif montage.kind == KIND_FORCED_ONLY:
            notes.append("this montage is kind 9: the game colours it only when forced; the material file's own "
                         "colours are what it shows by default")
            kind = "material"
        else:
            used = montage.used()
            number = default if default is not None else (0 if 0 in used else None)
            if number is None:
                notes.append("no item names a colour for this model and colour 0 is empty: the material file's own "
                             "colours")
                kind = "material"
            elif number % montage.variants not in used:
                notes.append(f"the item's colour {number} is empty in this montage (the game would paint its dyed "
                             "parts black): the material file's own colours")
                kind = "material"
            else:
                kind = "variant"
    if kind == "material":
        return {h: mm.own.check() for h, mm in masked.items()}, notes
    if kind == "rgb":
        return {h: ColourMask(mm.own.rate, mm.own.threshold, spec.colours).check() for h, mm in masked.items()}, notes
    if montage is None or not montage.variants:
        raise RiftError("this model has no montage colours; use default, material or #rrggbb")
    entries = montage.variant(number)
    if all(e.empty for e in entries):
        raise RiftError(f"colour {number} is empty for this model (it has {', '.join(map(str, montage.used()))})")
    if montage.kind == KIND_FORCED_ONLY:
        notes.append("this montage is kind 9: the game colours it only when forced (whether a dye reaches it in "
                     "game is UNKNOWN)")
    if material_names is None:
        raise RiftError("the model's material names are needed to place montage colours")
    by_index = {i: jamcrc(n) for i, n in enumerate(material_names)}
    out: dict[int, ColourMask] = {}
    for e in entries:                                  # in order: a later entry for the same material wins
        h = by_index.get(e.material)
        if h is None or h not in masked:
            continue
        mm = masked[h]
        out[h] = ColourMask(mm.own.rate, mm.own.threshold, e.colours).check()
    missing = [h for h in masked if h not in out]
    if missing:
        notes.append(f"{len(missing)} colour-mask material(s) have no entry in colour {number}: their own colours")
        for h in missing:
            out[h] = masked[h].own.check()
    return out, notes


# -- baking ---------------------------------------------------------------------------------------------
def _sampler(mw: int, mh: int, mask: bytes, w: int, h: int):
    """Bilinear lookup of the mask at each colour-map texel's centre, wrapping (as the sampler does)."""
    sx, sy = mw / w, mh / h

    def taps(n, size, scale):
        f = (n + 0.5) * scale - 0.5
        i0 = math.floor(f)
        t = f - i0
        return i0 % size, (i0 + 1) % size, t

    cols = [taps(x, mw, sx) for x in range(w)]
    rows = [taps(y, mh, sy) for y in range(h)]
    return cols, rows


def bake_rgba(w: int, h: int, rgba: bytes, mw: int, mh: int, mask: bytes, cm: ColourMask) -> tuple[bytes, bytearray]:
    """The colour map with the dye in it (alpha kept) and a per-texel 'changed' map."""
    if len(rgba) != w * h * 4 or len(mask) != mw * mh * 4 or min(w, h, mw, mh) < 1:
        raise RiftError("the pixel data does not match the size")
    cm.check()
    cols, rows = _sampler(mw, mh, mask, w, h)
    out = bytearray(rgba)
    changed = bytearray(w * h)
    cache: dict = {}
    for y in range(h):
        y0, y1, ty = rows[y]
        r0, r1 = y0 * mw * 4, y1 * mw * 4
        for x in range(w):
            x0, x1, tx = cols[x]
            a, b, c, d = r0 + 4 * x0, r0 + 4 * x1, r1 + 4 * x0, r1 + 4 * x1
            if mask[a:a + 3] == mask[b:b + 3] == mask[c:c + 3] == mask[d:d + 3]:
                key = bytes(mask[a:a + 3])
                s = cache.get(key)
                if s is None:
                    f = cm.factors(mask[a] / 255.0, mask[a + 1] / 255.0, mask[a + 2] / 255.0)
                    s = cache[key] = tuple(math.sqrt(max(0.0, v)) for v in f)
            else:
                m = [((mask[a + k] * (1 - tx) + mask[b + k] * tx) * (1 - ty)
                      + (mask[c + k] * (1 - tx) + mask[d + k] * tx) * ty) / 255.0 for k in range(3)]
                s = tuple(math.sqrt(max(0.0, v)) for v in cm.factors(*m))
            if s == (1.0, 1.0, 1.0):
                continue
            o = 4 * (y * w + x)
            for k in range(3):
                v = rgba[o + k]
                nv = min(255, int(v * s[k] + 0.5))
                if nv != v:
                    out[o + k] = nv
                    changed[y * w + x] = 1
    return bytes(out), changed


def _dirty(changed: bytearray, w: int, h: int, levels: int) -> list:
    """Per mip level, which 4x4 blocks cover a changed texel of the top level (rows of booleans in the level's
    own block grid).  Sides that are not powers of two make every block below the top dirty (halving drops
    texels there, so footprints are not exact)."""
    def grid(level):
        lw, lh = max(1, w >> level), max(1, h >> level)
        return max(1, (lw + 3) // 4), max(1, (lh + 3) // 4)

    bw, bh = grid(0)
    top = [[False] * bw for _ in range(bh)]
    for y in range(h):
        row = changed[y * w:(y + 1) * w]
        if any(row):
            line = top[y // 4]
            for x, c in enumerate(row):
                if c:
                    line[x // 4] = True
    out = [top]
    pow2 = not (w & (w - 1) or h & (h - 1))
    for level in range(1, levels):
        nw, nh = grid(level)
        if not pow2:
            out.append([[True] * nw for _ in range(nh)])
            continue
        prev = out[-1]
        pw, ph = len(prev[0]), len(prev)
        out.append([[any(prev[yy][xx] for yy in (2 * y, 2 * y + 1) if yy < ph for xx in (2 * x, 2 * x + 1) if xx < pw)
                     for x in range(nw)] for y in range(nh)])
    return out


def _bc1(blk: list, transparent: bool) -> bytes:
    """A BC1 block; with `transparent`, the end points fit the opaque texels only (the transparent ones are
    index 3, as texcodec writes them)."""
    if not transparent:
        return texcodec._color_block(blk)
    solid = [q for q in blk if q[3] >= 128] or blk
    lo = tuple(min(q[c] for q in solid) for c in range(3))
    hi = tuple(max(q[c] for q in solid) for c in range(3))
    c0, c1 = texcodec._pack565(*lo), texcodec._pack565(*hi)     # c0 <= c1: three colours + transparent
    if c0 > c1:
        c0, c1, lo, hi = c1, c0, hi, lo
    dr, dg, db = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    dd = dr * dr + dg * dg + db * db or 1
    idx = 0
    for i, q in enumerate(blk):
        if q[3] < 128:
            k = 3
        else:
            t = ((q[0] - lo[0]) * dr + (q[1] - lo[1]) * dg + (q[2] - lo[2]) * db) / dd
            k = 0 if t < 0.25 else 2 if t < 0.75 else 1              # c0 = lo, midpoint, c1 = hi
        idx |= k << (2 * i)
    return struct.pack("<HHI", c0, c1, idx)


def bake_texture(albedo: bytes, mask: bytes, cm: ColourMask, version: int | None = None) -> tuple[bytes, int]:
    """A colour map (.tex) with the dye baked in: the same format, size, mip count and attributes (``version``:
    another game's revision, with that game's attribute bits).  Returns (texture, texels changed).  Blocks
    at any mip whose footprint no dye reaches keep their bytes; when nothing changes, the input comes back."""
    t = tex.parse(albedo)
    if t.is_cube or tex._pixels_contiguous(t) is None:
        raise RiftError("a dyed colour map is a flat texture with its mips in order")
    if not texcodec.writable(t.fmt):
        raise RiftError(f"{texcodec.codec(t.fmt)} colour maps cannot be written back (BC1/BC3 only)")
    if max(t.width, t.height) > texcodec.MAX_SIDE:
        raise RiftError(f"a colour map's sides are at most {texcodec.MAX_SIDE}")
    mt = tex.parse(mask)
    if mt.is_cube or max(mt.width, mt.height) > texcodec.MAX_SIDE:
        raise RiftError("the colour mask is not a flat texture of a size Riftstone reads")
    w, h, px = texcodec.decode(t, 0)
    mw, mh, mpx = texcodec.decode(mt, 0)
    baked, changed = bake_rgba(w, h, px, mw, mh, mpx, cm)
    n = sum(changed)
    if version is not None and version != t.version:
        if version not in tex.VERSIONS:
            raise RiftError(f"texture revision 0x{version:x} is not one Riftstone writes")
        t.attr1, t.version = tex.attr1_for(t.attr1, version), version
    if not n:
        return tex.build(t), 0
    bc3 = t.fmt in texcodec.BC3
    size = tex._BLOCK[t.fmt]
    cutout = t.fmt in texcodec.BC1 and texcodec.has_cutout(t)
    body = bytearray(t.body)
    pos = 4 * t.mip_count
    cw, ch, cur = w, h, baked
    dirty = _dirty(changed, w, h, t.mip_count)
    for k in range(t.mip_count):
        msize = tex._mip_size(t.width, t.height, k, t.fmt)
        bw = max(1, (cw + 3) // 4)
        for by, line in enumerate(dirty[k]):
            for bx, d in enumerate(line):
                if not d:
                    continue
                blk = texcodec._block(cur, cw, ch, bx * 4, by * 4)
                enc = (texcodec._alpha_block(blk) + texcodec._color_block(blk)) if bc3 else \
                    _bc1(blk, cutout and any(q[3] < 128 for q in blk))
                o = pos + (by * bw + bx) * size
                body[o:o + size] = enc
        pos += msize
        if k + 1 < t.mip_count:
            cw, ch, cur = texcodec._half(cw, ch, cur)
    t.body = bytes(body)
    out = tex.build(t)
    if tex._pixels_contiguous(tex.parse(out)) is None:         # the layout the game and to_dds expect
        raise RiftError("internal: the dyed texture's mips do not fit")
    return out, n


def dyed_name(albedo: str, mask: str, cm: ColourMask) -> str:
    """The name a dyed copy of a colour map gets (one per colour set; the same colours give the same name)."""
    h = zlib.crc32(albedo.encode("latin-1") + b"\0" + mask.encode("latin-1") + b"\0" + cm.key())
    return f"{albedo}_d{h:08x}"


# -- the client: items, models, montages ---------------------------------------------------------------
@dataclass(frozen=True)
class Equip:
    """An equipment item as the item list and the model table describe it."""
    id: int
    kind: str                      # weapon | armour
    name: str
    tag: int
    colour: int                    # the colour number it is worn in (its group's mColorNo)
    parts: int
    sex: int                       # the group's sex type: 1 any, 2 male, 3 female


@dataclass
class Catalog:
    items: ItemList
    restable: list
    names: list                    # item name text by name id
    _equipment: list | None = None

    def equipment(self) -> list[Equip]:
        """Every weapon and armour item with its group's look (an item naming no group is left out)."""
        if self._equipment is None:
            out = []
            for kind, gi in (("weapon", 10), ("armour", 12)):
                groups = self.items.groups(kind)
                for fields, _ in self.items.records[kind]:
                    g = fields[gi]
                    if g >= len(groups):
                        continue
                    gf = groups[g][0]
                    name = self.names[gf[3]] if gf[3] < len(self.names) else f"item {fields[0]}"
                    out.append(Equip(fields[0], kind, name, gf[0], gf[2], gf[1], gf[9]))
            self._equipment = out
        return self._equipment

    def entries(self, tag: int) -> list[ResEntry]:
        return [e for e in self.restable if e.tag == tag and e.arc]


def _client(game, idx, name: bytes, type_id: int) -> bytes:
    from . import arc

    arcs = idx.archives_with(name, type_id)
    if not arcs:
        raise RiftError(f"{name.decode('latin-1')}.{typemap.extension(type_id)} is not in {game.title}")
    e = arc.Archive.read(game.vanilla_arc(arcs[0])).find(name, type_id)
    if e is None:
        raise RiftError(f"{arcs[0]} does not hold {name.decode('latin-1')}")
    return e.data()


def load_catalog(game, idx) -> Catalog:
    from . import gmd

    items = read_itemlist(_client(game, idx, ITEM_LIST, ITL_TYPE))
    _, table = read_restable(_client(game, idx, RES_TABLE, WRT_TYPE))
    names = [m.text for m in gmd.parse(_client(game, idx, ITEM_NAMES, GMD_TYPE)).messages]
    return Catalog(items, table, names)


def _named(idx, h: int, type_id: int) -> bytes | None:
    """The resource of a type whose JAMCRC name is h (the model table refers to resources that way)."""
    for (n,) in idx.db.execute("SELECT DISTINCT name FROM res WHERE type=?", (type_id,)):
        if jamcrc(bytes(n)) == h:
            return bytes(n)
    return None


@dataclass
class Target:
    model: bytes                   # engine name of the .mod
    material: bytes                # its .mrl
    montage: bytes | None          # its .dmt
    item: Equip | None             # when asked for by item
    users: list                    # items worn with this model (for 'default')
    default: int | None            # the colour number 'default' means


def _refs(entry: ResEntry, type_id: int) -> int | None:
    return next((r & 0xFFFFFFFF for r in entry.refs if r >> 32 == type_id), None)


def _model_name(text: str) -> bytes:
    p = text.strip().replace("/", "\\")
    for ext in (".yaml", ".mod", ".mrl", ".dmt"):
        if p.lower().endswith(ext):
            p = p[:-len(ext)]
    return p.lstrip("\\").encode("latin-1")


def find_equipment(cat: Catalog, query: str) -> list[Equip]:
    q = query.strip().lower()
    eq = cat.equipment()
    if re.fullmatch(r"[0-9]{1,9}", q, re.ASCII):
        return [e for e in eq if e.id == int(q)]
    exact = [e for e in eq if e.name.lower() == q]
    return exact or [e for e in eq if q in e.name.lower()]


def resolve(game, idx, what: str, sex: str | None = None, catalog: Catalog | None = None) -> Target:
    """An Online equipment model by item (name or id) or by engine path, with its material, montage and the
    colour number 'default' means."""
    if not what or not what.strip():
        raise RiftError("which item or model? (a name, an item id or a model path)")
    cat = catalog or load_catalog(game, idx)
    wanted_sex = {None: None, "": None, "male": 1, "m": 1, "female": 2, "f": 2}.get((sex or "").strip().lower(), -1)
    if wanted_sex == -1:
        raise RiftError("sex is male or female")
    if "\\" in what or "/" in what:
        model = _model_name(what)
        if not idx.archives_with(model, MOD_TYPE):
            raise RiftError(f"{model.decode('latin-1')}.mod is not in {game.title}")
        h = jamcrc(model)
        entries = [e for e in cat.restable if _refs(e, MOD_TYPE) == h]
        tags = {e.tag for e in entries}
        users = [e for e in cat.equipment() if e.tag in tags]
        montage = None
        for e in entries:
            d = _refs(e, DMT_TYPE)
            if d is not None:
                montage = _named(idx, d, DMT_TYPE)
                if montage:
                    break
        if montage is None and idx.archives_with(model, DMT_TYPE):
            montage = model
        default = Counter(u.colour for u in users).most_common(1)[0][0] if users else None
        return Target(model, model, montage, None, users, default)
    hits = find_equipment(cat, what)
    if not hits:
        raise RiftError(f"no Online weapon or armour is called {what!r}")
    looks = sorted({(e.tag, e.colour, e.sex) for e in hits})
    if len(looks) > 1:
        shown = "; ".join(f"{e.name} (item {e.id})" for e in hits[:8])
        raise RiftError(f"{len(hits)} items match {what!r} with {len(looks)} different looks ({shown}"
                        f"{' ...' if len(hits) > 8 else ''}): give an item id")
    item = hits[0]
    entries = cat.entries(item.tag)
    if not entries:
        raise RiftError(f"{item.name} (item {item.id}) names model tag {item.tag}, which the model table lacks")
    body = {2: 1, 3: 2}.get(item.sex)                   # the item's own restriction first
    if body is None:
        body = wanted_sex or 1
    elif wanted_sex and wanted_sex != body:
        raise RiftError(f"{item.name} is made for {'men' if body == 1 else 'women'} only")
    pick = [e for e in entries if e.sex == body] or [e for e in entries if e.sex == 0] or entries
    e = pick[0]
    mh, dh = _refs(e, MOD_TYPE), _refs(e, DMT_TYPE)
    model = _named(idx, mh, MOD_TYPE) if mh is not None else None
    if model is None:
        raise RiftError(f"{item.name}'s model ({e.arc}) is not in this client")
    montage = _named(idx, dh, DMT_TYPE) if dh is not None else None
    users = [u for u in cat.equipment() if u.tag == item.tag]
    return Target(model, model, montage, item, users, item.colour)


@dataclass
class Look:
    """Everything dyeing a model needs, read from the client."""
    target: Target
    model_data: bytes
    material_data: bytes
    montage: Montage | None
    masked: dict
    material_names: list


def load_look(game, idx, target: Target, material: bytes | None = None) -> Look:
    from . import port

    mdl = _client(game, idx, target.model, MOD_TYPE)
    mat = _client(game, idx, material or target.material, MRL_TYPE)
    mon = parse_montage(_client(game, idx, target.montage, DMT_TYPE)) if target.montage else None
    return Look(target, mdl, mat, mon, masked_materials(mat), port.model_info(mdl).material_names)


def describe(look: Look) -> list[str]:
    """Lines saying what the model's colours are: the default, every colour number with colours, the dyes."""
    t = look.target
    lines = [f"model {t.model.decode('latin-1')}" + (f"  (montage {t.montage.decode('latin-1')})" if t.montage else
                                                     "  (no montage)")]
    if t.item:
        lines.append(f"item {t.item.id} {t.item.name}: worn in colour {t.item.colour}")
    elif t.users:
        cnt = Counter(u.colour for u in t.users)
        lines.append(f"{len(t.users)} item(s) wear this model; colours " +
                     ", ".join(f"{c} ({n})" for c, n in sorted(cnt.items())))
    names = {jamcrc(n): n.decode("latin-1") for n in look.material_names}
    lines.append(f"{len(look.masked)} of {len(look.material_names)} material(s) carry a colour mask:")
    for h, mm in look.masked.items():
        lines.append(f"  {names.get(h, hex(h))}: {mm.albedo} + {mm.mask}; its own colours "
                     + " ".join(hex_of(c) for c in mm.own.colours))
    mon = look.montage
    if mon is not None:
        dyes = {v: k for k, v in DYES.items()}
        lines.append(f"montage kind {mon.kind}, {mon.variants} colour number(s):")
        for v in mon.used():
            cols = sorted({hex_of(c) for e in mon.variant(v) if not e.empty for c in e.colours})
            label = f" {dyes[v]} dye" if v in dyes else ""
            lines.append(f"  {v:2d}{label}: " + " ".join(cols[:9]) + (" ..." if len(cols) > 9 else ""))
    return lines


# -- writing: a folder of maps, or the textures a port wrote ---------------------------------------------------
@dataclass
class Baked:
    name: str                      # the dyed map's engine name
    albedo: str
    mask: str
    colours: ColourMask
    materials: list                # material hashes that use it
    data: bytes
    changed: int


def bake_look(game, idx, look: Look, spec: Spec, version: int | None = tex.VERSION) -> tuple[list[Baked], list[str]]:
    """Every colour-mask map of a model baked for a spec: one per (map, mask, colours)."""
    colours, notes = plan_colours(look.masked, look.material_names, look.montage, spec, look.target.default)
    groups: dict = {}
    for h, cm in colours.items():
        mm = look.masked[h]
        groups.setdefault((mm.albedo, mm.mask, cm), []).append(h)
    out = []
    cache: dict = {}
    for (albedo, mask, cm), hashes in groups.items():
        for n in (albedo, mask):
            if n not in cache:
                cache[n] = _client(game, idx, n.encode("latin-1"), TEX_TYPE)
        data, n = bake_texture(cache[albedo], cache[mask], cm, version)
        out.append(Baked(dyed_name(albedo, mask, cm), albedo, mask, cm, hashes, data, n))
    return out, notes


def _safe_file(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def write_folder(folder: Path, baked: list[Baked], look: Look, kind: str = "tex") -> list[Path]:
    """The dyed maps as files: <map>.tex (Dark Arisen's revision), .png or .dds; a map dyed two ways gets
    <map>_<material>.<kind> for each way."""
    if kind not in ("tex", "png", "dds"):
        raise RiftError("write the maps as tex, png or dds")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    names = {jamcrc(n): n.decode("latin-1") for n in look.material_names}
    per_map = Counter(b.albedo for b in baked)
    written = []
    for b in baked:
        base = b.albedo.rsplit("\\", 1)[-1]
        if per_map[b.albedo] > 1:
            base += "_" + _safe_file(names.get(b.materials[0], f"{b.materials[0]:08x}"))
        t = tex.parse(b.data)
        if kind == "tex":
            payload = b.data
        elif kind == "dds":
            payload = tex.to_dds(t)
        else:
            w, h, px = texcodec.decode(t, 0)
            payload = texcodec.png(w, h, px)
        p = folder / f"{_safe_file(base)}.{kind}"
        p.write_bytes(payload)
        written.append(p)
    return written


def snapshot(root: Path) -> dict:
    """Every file under a mod's files/ and archives/ with its size and time (to see what a port wrote)."""
    out = {}
    root = Path(root)
    for top in ("files", "archives"):
        base = root / top
        if base.is_dir():
            for f in base.rglob("*"):
                if f.is_file():
                    st = f.stat()
                    out[f] = (st.st_size, st.st_mtime_ns)
    return out


def changed_since(root: Path, before: dict) -> list[Path]:
    return sorted(p for p, st in snapshot(root).items() if before.get(p) != st)


@dataclass
class PortDye:
    written: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _tex_file(root: Path, path: Path) -> tuple[str, Path] | None:
    """(engine name, archive folder) of a texture file inside a mod's archives/<archive>.arc/, else None."""
    from . import fsmap

    base = Path(root) / "archives"
    try:
        rel = path.relative_to(base)
    except ValueError:
        return None
    parts = rel.parts
    arc_end = next((i for i, p in enumerate(parts[:-1]) if p.lower().endswith(".arc")), None)
    if arc_end is None or not path.name.lower().endswith(".tex"):
        return None
    try:
        name, tid = fsmap.decode_path("/".join(parts[arc_end + 1:]))
    except RiftError:
        return None
    if tid != TEX_TYPE:
        return None
    return name.decode("latin-1"), base.joinpath(*parts[:arc_end + 1])


def repoint(raw: bytes, changes: dict[int, str]) -> bytes:
    """A material file with the colour map of some materials (by material hash) pointed at other textures.
    The texture table keeps only what some binding uses, in order, so it lists nothing a mod no longer holds (a
    listed texture the archive lacks may be looked up as a loose file, which is fatal in Dark Arisen)."""
    m = mrl.parse(raw)
    parts = mrl.blocks(raw, m)
    textures = list(m.textures)
    where = {t.name: i + 1 for i, t in enumerate(textures)}
    cmds = []
    for x, (cmd, anim) in zip(m.materials, parts):
        cmd = bytearray(cmd)
        target = changes.get(x.material_hash)
        for n, b in enumerate(mrl.bindings(raw, x)):
            if target is not None and b.kind == mrl.SET_TEXTURE and b.slot == SLOT_ALBEDO and \
                    0 < b.value <= len(m.textures):
                if target not in where:
                    like = textures[b.value - 1]
                    t = mrl.Texture(like.type_id, like.a, like.b, b"")
                    t.set_name(target)
                    textures.append(t)
                    where[target] = len(textures)
                struct.pack_into("<I", cmd, n * mrl.CMD.size + 4, where[target])
        cmds.append((cmd, anim, mrl.bindings(raw, x)))
    used = sorted({struct.unpack_from("<I", cmd, n * mrl.CMD.size + 4)[0] for cmd, _, binds in cmds
                   for n, b in enumerate(binds) if b.kind == mrl.SET_TEXTURE} - {0})
    keep = [i for i in used if i <= len(textures)]
    renumber = {old: new + 1 for new, old in enumerate(keep)}
    new_parts = []
    for cmd, anim, binds in cmds:
        for n, b in enumerate(binds):
            if b.kind == mrl.SET_TEXTURE:
                v = struct.unpack_from("<I", cmd, n * mrl.CMD.size + 4)[0]
                if v in renumber:
                    struct.pack_into("<I", cmd, n * mrl.CMD.size + 4, renumber[v])
        new_parts.append((bytes(cmd), anim))
    table = [textures[i - 1] for i in keep] if changes else textures
    if not changes:
        new_parts = [(bytes(c), a) for c, a, _ in cmds]
    out = mrl.assemble(m.version, m.type_hash, table, m.materials, new_parts)
    mrl.parse(out)
    return out


def port_plan(resource: str, colour: str, src_game, src_idx, dst_kind: str, model_only: bool = False) -> tuple[Spec, Look]:
    """What ``riftstone port <model> --dye <colour>`` will bake, checked before the port writes anything."""
    from . import fsmap

    if src_game.kind != "ddo" or dst_kind != "ddda":
        raise RiftError("--dye bakes Online's equipment colours into a Dark Arisen mod: port an Online (ddo) model "
                        "into a ddda mod")
    if model_only:
        raise RiftError("--dye needs the model's material and colour maps: leave out --model-only")
    path = resource.replace("\\", "/")
    if path.lower().endswith(".yaml"):
        path = path[:-5]
    try:
        _, tid = fsmap.decode_path(path)
    except RiftError:
        tid = None
    if tid != MOD_TYPE:
        raise RiftError("--dye goes with a model (.mod): its material says which maps the colours go on")
    spec = parse_spec(colour)
    look = load_look(src_game, src_idx, resolve(src_game, src_idx, resource))
    return spec, look


def dye_port(mod_root: Path, look: Look, spec: Spec, wrote: list[Path], game=None, idx=None) -> PortDye:
    """After ``port`` brought an Online model into a Dark Arisen mod: bake the dye into its colour maps.

    ``wrote`` are the files the port wrote.  Every rebuilt material among them whose colour map is one of the
    model's colour-mask maps (``ddo\\<map>``) is pointed at a dyed copy of that map (``ddo\\<map>_d<crc>``,
    one per colour set: materials sharing a map can wear different colours), written into every archive of
    the mod that holds the map.  The undyed copies this port wrote and no material of the mod uses any more
    are removed.  A material the port did not write is not touched."""
    from . import arcfolder, fsmap

    root = Path(mod_root)
    res = PortDye()
    colours, notes = plan_colours(look.masked, look.material_names, look.montage, spec, look.target.default)
    res.notes += notes
    if not colours:
        return res
    mrls = []
    for p in wrote:
        if p.suffix.lower() != ".mrl" or not p.is_file():
            continue
        raw = p.read_bytes()
        try:
            mrls.append((p, raw, mrl.parse(raw)))
        except RiftError:
            continue
    if not mrls:
        raise RiftError("the port wrote no material to dye (a model's material comes along unless --model-only)")
    homes: dict[str, list[tuple[Path, Path]]] = {}      # engine name -> [(file, its archive folder)]
    for p in snapshot(root):
        found = _tex_file(root, p)
        if found is not None:
            homes.setdefault(found[0], []).append((p, found[1]))
    baked: dict[tuple, tuple[str, bytes, int]] = {}
    cache: dict[str, bytes] = {}
    edits = []
    for p, raw, m in mrls:
        changes = {}
        for x in m.materials:
            h = x.material_hash
            mm, cm = look.masked.get(h), colours.get(h)
            if mm is None or cm is None:
                continue
            shown = [m.textures[b.value - 1].name for b in mrl.bindings(raw, x)
                     if b.kind == mrl.SET_TEXTURE and b.slot == SLOT_ALBEDO and 0 < b.value <= len(m.textures)]
            if "ddo\\" + mm.albedo not in shown:
                continue
            key = (mm.albedo, mm.mask, cm)
            if key not in baked:
                for n in (mm.albedo, mm.mask):
                    if n not in cache:
                        if game is None or idx is None:
                            raise RiftError("the Online client is needed to read the colour maps")
                        cache[n] = _client(game, idx, n.encode("latin-1"), TEX_TYPE)
                data, count = bake_texture(cache[mm.albedo], cache[mm.mask], cm, tex.VERSION)
                name = "ddo\\" + dyed_name(mm.albedo, mm.mask, cm)
                if len(name.encode("latin-1")) >= mrl.NAME_LEN:
                    raise RiftError(f"{name} is too long for a texture name (63 characters at most)")
                baked[key] = (name, data, count)
            changes[h] = baked[key][0]
        if changes:
            edits.append((p, raw, changes))
    if not edits:
        res.notes.append("no material the port wrote shows a colour-mask map of this model: nothing dyed")
        return res
    for (albedo, _mask, cm), (name, data, count) in baked.items():
        places = homes.get("ddo\\" + albedo, [])
        if not places:
            raise RiftError(f"ddo\\{albedo} is in no archive of the mod, so its dyed copy has nowhere to go")
        for _file, arc_dir in places:
            out = arc_dir / fsmap.encode_name(name.encode("latin-1"), TEX_TYPE)
            arcfolder.write_file(out, data)
            res.written.append(out)
        res.notes.append(f"{name}: {count} texel(s) dyed, colours {' '.join(hex_of(c) for c in cm.colours)}")
    for p, raw, changes in edits:
        arcfolder.write_file(p, repoint(raw, changes))
        res.written.append(p)
    still = set()
    for p in snapshot(root):
        if p.suffix.lower() == ".mrl":
            try:
                still.update(t.name for t in mrl.parse(p.read_bytes()).textures)
            except (RiftError, OSError):
                continue
    fresh = set(wrote)
    maps = {"ddo\\" + albedo for albedo, _mask, _cm in baked}
    dyed = re.compile(r"_d[0-9a-f]{8}$")
    for n, places in homes.items():
        if n in still:
            continue
        undyed = n in maps
        stale = dyed.search(n) is not None and n[:-10] in maps          # an earlier --dye of this model
        for fp, _arc in places:
            if ((undyed and fp in fresh) or stale) and fp.is_file():
                fp.unlink()
                res.removed.append(fp)
    return res
